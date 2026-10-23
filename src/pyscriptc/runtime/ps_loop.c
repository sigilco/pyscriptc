/* The single-threaded event loop behind `asyncio.run`, sleep, gather and
create_task.

A coroutine frame is a heap object the compiler builds (coro.py). The loop
never names its layout beyond two fixed slots the IR pass guarantees on every
coroutine frame: field 0 is `.ps_state` (i64) and field 1 is `.ps_task`, a
callable cell `{ impl, env }` whose implementation is the frame's env-first
resume shim `coro.<name>.resume.clo(env, frame) -> i32`. Status 0 means the
coroutine is pending, 1 means done — the same numbers the guard transform
uses.

A task is the loop's unit of scheduling. It wraps a frame and outlives a
single step: the task record survives a suspension, so a handle to it stays
valid and `asyncio.create_task` can hand one to the caller. The handle the
program sees is the task pointer itself, so `await task` and `task.result()`
read the same record the loop drives.

`asyncio.sleep(d)` arms a timer row keyed by frame; the loop re-queues the
frame when the deadline passes. `asyncio.gather` registers several frames and
drives them together, so the children interleave exactly like CPython's.
*/

#include "ps_rt.h"

#include <stdlib.h>
#include <string.h>
#include <time.h>

/* Offsets into every coroutine frame (coro.py guarantees this layout). */
#define PS_TASK_OFF 8

typedef struct PsTask PsTask;

struct PsTask {
    int64_t (*impl)(void *, void *);
    void *env;
    void *frame;
    PsTask *next;
    /* Set once the frame's resume reported completion. */
    int32_t done;
    /* Set when the task is cancelled: the loop raises CancelledError into it
       at its next suspension point. */
    int32_t cancelled;
    /* The result the task produced, for `task.result()`. The record holds the
       raw value; the caller reads it through the same ABI the frame uses. */
    void *result;
    int64_t result_size;
    /* The exception the task raised, if any. A task that fails does not kill
       the process: the exception waits in the record for whoever awaits it. */
    int32_t exc_code;
    void *exc;
    /* The frames parked on this task, waiting for it to finish. */
    void **waiters;
    int64_t waiter_len;
    int64_t waiter_cap;
};

typedef struct PsTimer {
    void *frame;
    double deadline;
    int64_t seq;
} PsTimer;

/* A waiter row: `parent` is parked until `child` completes. */
typedef struct PsWait {
    void *child;
    void *parent;
} PsWait;

static PsTask *ps_ready_head;
static PsTask *ps_ready_tail;
static PsTimer *ps_timers;
static int64_t ps_timer_len;
static int64_t ps_timer_cap;
static int64_t ps_timer_seq;
static PsWait ps_waits[64];
static int64_t ps_wait_len;
/* Every task the loop knows about, so `asyncio.run` can wait for the
   outstanding ones and free them at the end. */
static PsTask **ps_tasks;
static int64_t ps_task_len;
static int64_t ps_task_cap;
/* The task the running coroutine belongs to. A coroutine that awaits
   something that is not a plain child frame (a task handle, a lock) needs
   the loop to know which task to park, so the frame records it. */
static void *ps_current_task;

void ps_loop_reset(void) {
    ps_ready_head = ps_ready_tail = NULL;
    ps_timer_len = 0;
    ps_timer_seq = 0;
    ps_wait_len = 0;
    for (int64_t i = 0; i < ps_task_len; i++) {
        free(ps_tasks[i]->waiters);
        free(ps_tasks[i]->result);
        free(ps_tasks[i]);
    }
    ps_task_len = 0;
    ps_current_task = NULL;
}

static void ps_ready_push(PsTask *task) {
    task->next = NULL;
    if (ps_ready_tail == NULL) {
        ps_ready_head = ps_ready_tail = task;
        return;
    }
    ps_ready_tail->next = task;
    ps_ready_tail = task;
}

/* Read `.ps_task` (field 1) out of a frame: the callable cell holding the
resume shim. The cell is { impl, env }. */
static void ps_task_of(void *frame, int64_t (**impl)(void *, void *), void **env) {
    void *cell = *(void **)((char *)frame + PS_TASK_OFF);
    *impl = *(int64_t (**)(void *, void *))cell;
    *env = *(void **)((char *)cell + 8);
}

/* Put a frame on the ready queue. The frame keeps its task when it has one
(so the task record survives the step), otherwise the loop makes it a task of
its own. */
static void ps_ready_task(void *frame);

/* Track a task so the loop can wait for it and free it later. */
static void ps_track_task(PsTask *task) {
    if (ps_task_len == ps_task_cap) {
        int64_t cap = ps_task_cap == 0 ? 16 : ps_task_cap * 2;
        PsTask **grown = realloc(ps_tasks, (size_t)cap * sizeof(PsTask *));
        if (grown == NULL) {
            abort();
        }
        ps_tasks = grown;
        ps_task_cap = cap;
    }
    ps_tasks[ps_task_len++] = task;
}

/* The task that wraps a frame, if the loop already knows one. */
static PsTask *ps_task_for_frame(void *frame) {
    for (int64_t i = 0; i < ps_task_len; i++) {
        if (ps_tasks[i]->frame == frame) {
            return ps_tasks[i];
        }
    }
    return NULL;
}

/* Park a frame on a task: the frame resumes when the task completes. */
static void ps_task_add_waiter(PsTask *task, void *frame) {
    if (task->waiter_len == task->waiter_cap) {
        int64_t cap = task->waiter_cap == 0 ? 4 : task->waiter_cap * 2;
        void **grown = realloc(task->waiters, (size_t)cap * sizeof(void *));
        if (grown == NULL) {
            abort();
        }
        task->waiters = grown;
        task->waiter_cap = cap;
    }
    task->waiters[task->waiter_len++] = frame;
}

/* Run one ready task once. 1 when the task finished, 0 while pending. A
finished child releases its waiter: the parent re-polls and proceeds. */
static int64_t ps_task_step(PsTask *task) {
    int64_t status = task->impl(task->env, task->frame);
    if (status == 1) {
        task->done = 1;
        int64_t kept = 0;
        for (int64_t i = 0; i < ps_wait_len; i++) {
            if (ps_waits[i].child == task->frame) {
                ps_ready_task(ps_waits[i].parent);
                continue;
            }
            ps_waits[kept++] = ps_waits[i];
        }
        ps_wait_len = kept;
        /* The frames parked on this task wake up now. */
        for (int64_t i = 0; i < task->waiter_len; i++) {
            ps_ready_task(task->waiters[i]);
        }
        task->waiter_len = 0;
    }
    return status;
}

/* `await child` in a coroutine that just found the child pending: park the
parent until the child completes. */
void ps_loop_wait(void *child, void *parent) {
    for (int64_t i = 0; i < ps_wait_len; i++) {
        if (ps_waits[i].parent == parent) {
            ps_waits[i].child = child;
            return;
        }
    }
    if (ps_wait_len < 64) {
        ps_waits[ps_wait_len].child = child;
        ps_waits[ps_wait_len].parent = parent;
        ps_wait_len++;
    }
}

static double ps_now(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (double)ts.tv_sec + (double)ts.tv_nsec / 1e9;
}

static void ps_arm(void *frame, double delay) {
    if (ps_timer_len == ps_timer_cap) {
        int64_t cap = ps_timer_cap == 0 ? 8 : ps_timer_cap * 2;
        PsTimer *grown = realloc(ps_timers, (size_t)cap * sizeof(PsTimer));
        if (grown == NULL) {
            abort();
        }
        ps_timers = grown;
        ps_timer_cap = cap;
    }
    ps_timers[ps_timer_len].frame = frame;
    ps_timers[ps_timer_len].deadline = ps_now() + delay;
    ps_timers[ps_timer_len].seq = ps_timer_seq++;
    ps_timer_len++;
}

/* Move due timers' frames back onto the ready queue, earliest deadline first
(sleeping past several deadlines fires them in deadline order, like CPython's
timer heap). */
static int32_t ps_fire_due(void) {
    double now = ps_now();
    int32_t fired = 0;
    for (;;) {
        int64_t best = -1;
        for (int64_t i = 0; i < ps_timer_len; i++) {
            if (ps_timers[i].deadline > now) {
                continue;
            }
            if (best == -1) {
                best = i;
                continue;
            }
            int32_t better = ps_timers[i].deadline < ps_timers[best].deadline;
            if (!better && ps_timers[i].deadline == ps_timers[best].deadline) {
                /* Ties break by arm order, like CPython's stable timer heap. */
                better = ps_timers[i].seq < ps_timers[best].seq;
            }
            if (better) {
                best = i;
            }
        }
        if (best == -1) {
            break;
        }
        void *frame = ps_timers[best].frame;
        /* The frame may already own a task (a `create_task` that slept): reuse
           it, so the task survives the suspension. */
        PsTask *task = ps_task_for_frame(frame);
        if (task == NULL) {
            task = calloc(1, sizeof(PsTask));
            if (task == NULL) {
                abort();
            }
            ps_task_of(frame, &task->impl, &task->env);
            task->frame = frame;
            ps_track_task(task);
        }
        ps_ready_push(task);
        fired = 1;
        ps_timers[best] = ps_timers[ps_timer_len - 1];
        ps_timer_len--;
    }
    return fired;
}

/* Put a frame on the ready queue. The frame keeps its task when it has one
(so the task record survives the step), otherwise the loop makes it a task of
its own. */
static void ps_ready_task(void *frame) {
    PsTask *task = ps_task_for_frame(frame);
    if (task == NULL) {
        task = calloc(1, sizeof(PsTask));
        if (task == NULL) {
            abort();
        }
        ps_task_of(frame, &task->impl, &task->env);
        task->frame = frame;
        ps_track_task(task);
    }
    ps_ready_push(task);
}

/* Drain the ready queue once: every task runs until it suspends (sleep) or
finishes. Then sleep until the earliest timer and repeat until everything is
done. `blocking` picks run-to-completion (1) or a single drain (0). */
static void ps_loop_drive(int32_t blocking) {
    for (;;) {
        while (ps_ready_head != NULL) {
            PsTask *task = ps_ready_head;
            ps_ready_head = task->next;
            if (ps_ready_head == NULL) {
                ps_ready_tail = NULL;
            }
            void *saved = ps_current_task;
            ps_current_task = task;
            ps_task_step(task);
            ps_current_task = saved;
        }
        if (ps_timer_len == 0) {
            return;
        }
        if (!blocking) {
            return;
        }
        double earliest = ps_timers[0].deadline;
        for (int64_t i = 1; i < ps_timer_len; i++) {
            if (ps_timers[i].deadline < earliest) {
                earliest = ps_timers[i].deadline;
            }
        }
        double now = ps_now();
        if (earliest > now) {
            struct timespec req;
            double wait = earliest - now;
            req.tv_sec = (time_t)wait;
            req.tv_nsec = (long)((wait - (double)req.tv_sec) * 1e9);
            nanosleep(&req, NULL);
        }
        ps_fire_due();
    }
}

/* `asyncio.sleep(d)` inside a coroutine: arm a timer for the calling frame.
The coroutine suspends right after (the compiler emits the return 0), and the
loop re-queues it when the deadline passes. */
void ps_loop_sleep(void *frame, double delay) {
    ps_arm(frame, delay);
}

/* `asyncio.run(main())`: reset the loop, make the root frame ready, and run
to completion. */
void ps_loop_run_root(void *frame) {
    ps_loop_reset();
    ps_ready_task(frame);
    ps_loop_drive(1);
}

/* `asyncio.gather(c1, ...)`: reset the loop, queue every frame, run until
all are done. */
void ps_loop_run_gather(int64_t n, void **frames) {
    ps_loop_reset();
    for (int64_t i = 0; i < n; i++) {
        ps_ready_task(frames[i]);
    }
    ps_loop_drive(1);
}

/* The gather lowering drives the loop itself: reset, one ready call per
coroutine, then run. */
void ps_loop_ready(void *frame) {
    ps_ready_task(frame);
}

void ps_loop_run(void) {
    ps_loop_drive(1);
}

/* --- the task lifetime ------------------------------------------------- */

void *ps_task_new(void *frame) {
    PsTask *task = calloc(1, sizeof(PsTask));
    if (task == NULL) {
        abort();
    }
    ps_task_of(frame, &task->impl, &task->env);
    task->frame = frame;
    task->done = 0;
    task->cancelled = 0;
    task->result = NULL;
    task->result_size = 0;
    task->exc_code = 0;
    task->exc = NULL;
    ps_track_task(task);
    /* The task starts on the next loop iteration, never inline: that is
       CPython's `create_task` semantics. */
    ps_ready_push(task);
    return task;
}

void *ps_task_current(void) {
    return ps_current_task;
}

void ps_task_set_current(void *task) {
    ps_current_task = task;
}

int64_t ps_task_done(void *task) {
    if (task == NULL) {
        return 1;
    }
    return ((PsTask *)task)->done;
}

int64_t ps_task_cancel(void *task) {
    if (task == NULL) {
        return 0;
    }
    PsTask *t = (PsTask *)task;
    if (t->done) {
        return 0;
    }
    t->cancelled = 1;
    return 1;
}

int64_t ps_task_cancelled(void *task) {
    if (task == NULL) {
        return 0;
    }
    return ((PsTask *)task)->cancelled;
}

/* Step a task once outside the ready queue: `create_task` schedules the task
   instead of running it inline, but a caller that wants the task to make
   progress right now (the root task, or a task awaited from outside the
   loop) drives it through this. */
int64_t ps_task_step_task(void *task) {
    if (task == NULL) {
        return 1;
    }
    PsTask *t = (PsTask *)task;
    void *saved = ps_current_task;
    ps_current_task = t;
    int64_t status = ps_task_step(t);
    ps_current_task = saved;
    return status;
}

void ps_task_store_result(void *task, void *value, int64_t size) {
    if (task == NULL) {
        return;
    }
    PsTask *t = (PsTask *)task;
    free(t->result);
    t->result = NULL;
    t->result_size = 0;
    if (value != NULL && size > 0) {
        t->result = malloc((size_t)size);
        if (t->result == NULL) {
            abort();
        }
        memcpy(t->result, value, (size_t)size);
        t->result_size = size;
    }
}

int64_t ps_task_result_size(void *task) {
    if (task == NULL) {
        return 0;
    }
    return ((PsTask *)task)->result_size;
}

void ps_task_load_result(void *task, void *out) {
    if (task == NULL) {
        return;
    }
    PsTask *t = (PsTask *)task;
    if (t->result != NULL && t->result_size > 0) {
        memcpy(out, t->result, (size_t)t->result_size);
    }
}

int64_t ps_task_exc_code(void *task) {
    if (task == NULL) {
        return 0;
    }
    return ((PsTask *)task)->exc_code;
}

void ps_task_set_exc(void *task, int32_t code) {
    if (task == NULL) {
        return;
    }
    ((PsTask *)task)->exc_code = code;
}

/* Raise the exception a failed task holds. The awaiting side calls this when
   it reads a task that failed: the exception belongs to the awaiter, not to
   the task. */
void ps_task_raise_exc(void *task) {
    if (task == NULL) {
        return;
    }
    PsTask *t = (PsTask *)task;
    int32_t code = t->exc_code != 0 ? t->exc_code : PS_RUNTIME;
    ps_raise_code(code);
}

/* `await task`: park the calling frame until the task completes. The task
   may already be done, in which case the caller does not park at all. */
int64_t ps_task_await(void *task, void *frame) {
    if (task == NULL) {
        return 1;
    }
    PsTask *t = (PsTask *)task;
    if (t->done) {
        return 1;
    }
    ps_task_add_waiter(t, frame);
    return 0;
}
