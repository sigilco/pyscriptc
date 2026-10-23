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

`asyncio.wait_for(aw, d)` runs the awaitable's frame as a task with a
deadline row pointing at the awaiting frame. When the deadline fires first
the loop cancels the task and marks the waiter's row `timed out`; the
awaiter's resume reads the flag and raises `TimeoutError` in the frame
(D2 boundary: the raise happens in the awaiting frame, so it crosses no
live setjmp outside the frame's own try).
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
    /* Set once the task ran its coroutine at least once. A task cancelled
       before its first step never runs its body. */
    int32_t ran;
    /* Set while the task sits on the ready queue, so a second push (a repeat
       cancel before the step runs) does not queue it twice. */
    int32_t queued;
    /* Set when the task is cancelled: the loop raises CancelledError into it
       at its next suspension point. */
    int32_t cancelled;
    /* Set when the record backs a task handle (`asyncio.create_task`). A
       handle outlives its awaiter, so its failure waits in the record for
       whoever awaits the task or reads its result. A plain child frame has
       no handle, and its failure waits in the ps_fail table instead. */
    int32_t handle;
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

/* A wait_for deadline: when it fires before the task completes, the task is
cancelled and the awaiting frame resumes with the timeout outcome. One row
per parked awaiter. */
typedef struct PsDeadline {
    PsTask *task;
    void *awaiter;
    double deadline;
    int64_t seq;
    /* Set when the deadline fired: the awaiter raises TimeoutError. Cleared
       rows are dead (the task finished first). */
    int32_t timed_out;
} PsDeadline;

static PsDeadline *ps_deadlines;
static int64_t ps_deadline_len;
static int64_t ps_deadline_cap;
static int64_t ps_deadline_seq;

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

/* The failure of a child coroutine. A child that raises after a suspension
   is driven by the loop, not by its awaiter, so the exception cannot unwind
   into the awaiter's frame on its own: the loop catches it, parks it here,
   and the awaiter re-raises it at its own await point. The record is keyed
   by the child frame, because that is the handle the awaiter holds. */
typedef struct PsFail {
    void *child;
    PsExc *exc;
} PsFail;

static PsFail *ps_fails;
static int64_t ps_fail_len;
static int64_t ps_fail_cap;

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

/* The task that is running right now. The cooperative primitives in
ps_async.c park the running task, so they need to name it. */
void *ps_loop_current_task(void) {
    return ps_current_task;
}

void ps_loop_reset(void) {
    ps_ready_head = ps_ready_tail = NULL;
    ps_timer_len = 0;
    ps_timer_seq = 0;
    ps_wait_len = 0;
    ps_deadline_len = 0;
    ps_deadline_seq = 0;
    ps_fail_len = 0;
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
    task->queued = 1;
    if (ps_ready_tail == NULL) {
        ps_ready_head = ps_ready_tail = task;
        return;
    }
    ps_ready_tail->next = task;
    ps_ready_tail = task;
}

/* Re-queue a task that a primitive woke up. The task is not on the ready
queue yet (the primitive parked it), so the push is unconditional. */
void ps_loop_wake_task(void *task) {
    if (task == NULL) {
        return;
    }
    PsTask *t = (PsTask *)task;
    if (t->queued) {
        return;
    }
    ps_ready_push(t);
}

/* Read `.ps_task` (field 1) out of a frame: the callable cell holding the
resume shim. The cell is { impl, env }. */
static void ps_task_of(void *frame, int64_t (**impl)(void *, void *), void **env) {
    void *cell = *(void **)((char *)frame + PS_TASK_OFF);
    *impl = *(int64_t (**)(void *, void *))cell;
    *env = *(void **)((char *)cell + 8);
}

/* Adapters for ps_exc_catch, which calls a `fn(payload)` shape: the task
   carries its own impl/env, the bare frame reads its cell. */
static int32_t ps_task_shim(void *payload) {
    PsTask *task = payload;
    return (int32_t)task->impl(task->env, task->frame);
}

static int32_t ps_frame_shim(void *payload) {
    int64_t (*impl)(void *, void *);
    void *env;
    ps_task_of(payload, &impl, &env);
    return (int32_t)impl(env, payload);
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

/* Park a failure the loop caught: the child raised, so its awaiter must see
   the exception at its own await point. One row per child frame. */
static void ps_fail_store(void *child, PsExc *exc);

/* Run one ready task once. 1 when the task finished, 0 while pending. A
finished child releases its waiter: the parent re-polls and proceeds.

A task that raises is caught here: the frame runs on the loop's own stack,
with no awaiter below it, so an exception that unwound further would find
no handler and kill the process. The exception is parked against the frame
instead (ps_fail_store), and the frame's awaiter re-raises it at its own
await point.

A failure with no awaiter is unhandled: nothing will ever re-raise it, so
the loop reports it and exits, the way an exception that reaches the top of
the program does. */
static int64_t ps_task_step(PsTask *task) {
    if (task->cancelled) {
        /* A cancelled task stops at its next step: the coroutine does not
           continue, and the waiters wake up to see the cancellation. */
        task->done = 1;
        task->cancelled = 2;
        for (int64_t i = 0; i < task->waiter_len; i++) {
            ps_ready_task(task->waiters[i]);
        }
        task->waiter_len = 0;
        return 1;
    }
    task->ran = 1;
    PsExc *caught = NULL;
    int32_t status = 0;
    if (ps_exc_catch(ps_task_shim, task, &status, &caught) != 0) {
        status = 2;
    }
    if (status == 1 || status == 2) {
        task->done = 1;
        int64_t kept = 0;
        int64_t awaiters = 0;
        for (int64_t i = 0; i < ps_wait_len; i++) {
            if (ps_waits[i].child == task->frame) {
                ps_ready_task(ps_waits[i].parent);
                awaiters++;
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
        if (status == 2) {
            if (task->handle) {
                /* A task handle keeps its own failure: whoever awaits the
                   task (or reads its result) is the one who sees it, so the
                   exception waits in the record. */
                task->exc = caught;
                task->exc_code = caught != NULL ? caught->code : 0;
            } else if (awaiters == 0) {
                /* Nothing awaits this frame, so the exception has no place to
                   go: report it and leave, like an exception that reaches the
                   top of the program. */
                ps_exc_restore(caught);
                ps_report_current(NULL);
                exit(1);
            } else {
                ps_fail_store(task->frame, caught);
            }
        }
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

/* --- the child failure channel ------------------------------------------ */

/* Park a failure the loop caught: the child raised, so its awaiter must see
   the exception at its own await point. One row per child frame. */
static void ps_fail_store(void *child, PsExc *exc) {
    for (int64_t i = 0; i < ps_fail_len; i++) {
        if (ps_fails[i].child == child) {
            ps_fails[i].exc = exc;
            return;
        }
    }
    if (ps_fail_len == ps_fail_cap) {
        int64_t cap = ps_fail_cap == 0 ? 8 : ps_fail_cap * 2;
        PsFail *grown = realloc(ps_fails, (size_t)cap * sizeof(PsFail));
        if (grown == NULL) {
            abort();
        }
        ps_fails = grown;
        ps_fail_cap = cap;
    }
    ps_fails[ps_fail_len].child = child;
    ps_fails[ps_fail_len].exc = exc;
    ps_fail_len++;
}

static PsExc *ps_fail_take(void *child) {
    for (int64_t i = 0; i < ps_fail_len; i++) {
        if (ps_fails[i].child == child) {
            PsExc *exc = ps_fails[i].exc;
            ps_fail_len--;
            for (int64_t j = i; j < ps_fail_len; j++) {
                ps_fails[j] = ps_fails[j + 1];
            }
            return exc;
        }
    }
    return NULL;
}

/* `await child` (coro.py's poll): drive the child once and tell the parent
   how it went. 0 pending, 1 done, 2 failed.

   The child's exception is the whole point of status 2. A child that raises
   while the loop drives it has no awaiter on the C stack, so the unwinder
   would walk into the loop's frames, find no handler, and kill the process
   (that was the bug: a `try` around `await` never caught the child's
   failure). Under `ps_exc_catch` the raise lands here instead: the loop
   parks the exception against the child frame and reports status 2. The
   parent then re-raises it at its own await point (ps_await_raise), where
   the parent's own try outline sees it. A child that raises on the parent's
   first poll unwinds into the parent directly and never reaches this. */
int64_t ps_await_poll(void *child, void *parent) {
    (void)parent;
    /* A failure parked by an earlier step: the parent is re-polling the same
       child, so report the failure again without driving the frame. */
    for (int64_t i = 0; i < ps_fail_len; i++) {
        if (ps_fails[i].child == child) {
            return 2;
        }
    }
    PsExc *caught = NULL;
    int32_t status = 0;
    if (ps_exc_catch(ps_frame_shim, child, &status, &caught) != 0) {
        ps_fail_store(child, caught);
        return 2;
    }
    return status;
}

/* Re-raise the child's failure in the caller (the awaiting parent). The
   parent runs this inside its own resume body, so its try outline sees the
   exception exactly where CPython's awaiter would. */
void ps_await_raise(void *child) {
    PsExc *exc = ps_fail_take(child);
    if (exc != NULL) {
        ps_raise_exc(exc);
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

/* --- wait_for deadlines -------------------------------------------------- */

/* Record a wait_for deadline: `task` runs under a deadline, and when the
deadline fires first the loop cancels the task and marks the row so the
awaiter's resume raises TimeoutError. */
static void ps_deadline_arm(PsTask *task, void *awaiter, double delay) {
    if (ps_deadline_len == ps_deadline_cap) {
        int64_t cap = ps_deadline_cap == 0 ? 8 : ps_deadline_cap * 2;
        PsDeadline *grown = realloc(ps_deadlines, (size_t)cap * sizeof(PsDeadline));
        if (grown == NULL) {
            abort();
        }
        ps_deadlines = grown;
        ps_deadline_cap = cap;
    }
    ps_deadlines[ps_deadline_len].task = task;
    ps_deadlines[ps_deadline_len].awaiter = awaiter;
    ps_deadlines[ps_deadline_len].deadline = ps_now() + delay;
    ps_deadlines[ps_deadline_len].seq = ps_deadline_seq++;
    ps_deadlines[ps_deadline_len].timed_out = 0;
    ps_deadline_len++;
}

/* Drop every deadline row parked on `awaiter` (the await finished, one way
or the other). */
static void ps_deadline_drop(void *awaiter) {
    int64_t kept = 0;
    for (int64_t i = 0; i < ps_deadline_len; i++) {
        if (ps_deadlines[i].awaiter == awaiter) {
            continue;
        }
        ps_deadlines[kept++] = ps_deadlines[i];
    }
    ps_deadline_len = kept;
}

/* The wait_for outcome an awaiter reads after its resume: 0 not timed out,
1 the deadline fired (the awaiter raises TimeoutError). */
static int32_t ps_deadline_fired(void *awaiter) {
    for (int64_t i = 0; i < ps_deadline_len; i++) {
        if (ps_deadlines[i].awaiter == awaiter && ps_deadlines[i].timed_out) {
            return 1;
        }
    }
    return 0;
}

/* Fire every expired wait_for deadline: cancel the task (a suspended task
stops at its next resume) and re-queue the awaiter, which reads the flag and
raises TimeoutError. Earliest first, ties by arm order, like the timer heap. */
static void ps_fire_deadlines(void) {
    double now = ps_now();
    for (;;) {
        int64_t best = -1;
        for (int64_t i = 0; i < ps_deadline_len; i++) {
            if (ps_deadlines[i].timed_out || ps_deadlines[i].deadline > now) {
                continue;
            }
            if (best == -1 || ps_deadlines[i].deadline < ps_deadlines[best].deadline ||
                (ps_deadlines[i].deadline == ps_deadlines[best].deadline && ps_deadlines[i].seq < ps_deadlines[best].seq)) {
                best = i;
            }
        }
        if (best == -1) {
            return;
        }
        PsDeadline *row = &ps_deadlines[best];
        row->timed_out = 1;
        /* The cancel wakes the awaiter: a task that never ran is finished at
           once (its waiters wake now), and a task that already ran stops at
           its next step (the cancel re-queues it, and that step wakes the
           waiters). The deadline row never queues the awaiter itself: doing
           both would run the awaiter twice. */
        ps_task_cancel(row->task);
        now = ps_now();
    }
}

static int32_t ps_deadlines_pending(void) {
    return ps_deadline_len > 0 ? 1 : 0;
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
        /* Expired wait_for deadlines fire before the queue drains: a
           deadline in the past cancels its task and re-queues the awaiter,
           so an awaiter parked on a past deadline never reads a result
           ahead of the timeout. */
        ps_fire_deadlines();
        /* The `to_thread` calls poll here: a worker that finished wakes
           the task that parked on it, so the wakeup lands before the next
           drain. */
        ps_to_thread_pump();
        while (ps_ready_head != NULL) {
            PsTask *task = ps_ready_head;
            ps_ready_head = task->next;
            if (ps_ready_head == NULL) {
                ps_ready_tail = NULL;
            }
            task->queued = 0;
            void *saved = ps_current_task;
            ps_current_task = task;
            ps_task_step(task);
            ps_current_task = saved;
        }
        if (ps_timer_len == 0 && !ps_deadlines_pending() && !ps_threads_pending()) {
            return;
        }
        if (!blocking) {
            return;
        }
        double earliest = -1.0;
        for (int64_t i = 0; i < ps_timer_len; i++) {
            if (earliest < 0.0 || ps_timers[i].deadline < earliest) {
                earliest = ps_timers[i].deadline;
            }
        }
        for (int64_t i = 0; i < ps_deadline_len; i++) {
            if (ps_deadlines[i].timed_out) {
                continue;
            }
            if (earliest < 0.0 || ps_deadlines[i].deadline < earliest) {
                earliest = ps_deadlines[i].deadline;
            }
        }
        if (earliest < 0.0) {
            /* No timer to wait on, but a thread call may still be running.
               The loop spins until the worker finishes: the loop does not
               poll the thread, so this is the honest boundary of the
               `to_thread` stage (the stage D3 breakdown records it). */
            if (!ps_threads_pending()) {
                return;
            }
            struct timespec pause = {0, 1000000};
            nanosleep(&pause, NULL);
            ps_fire_deadlines();
            continue;
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
        ps_fire_deadlines();
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
    task->handle = 1;
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

/* `task.done()`: True when the task finished. A task with a pending cancel is
   not done yet: the cancellation is delivered at the task's next step, so
   CPython reports False until then. */
int64_t ps_task_done(void *task) {
    if (task == NULL) {
        return 1;
    }
    PsTask *t = (PsTask *)task;
    if (t->cancelled == 1) {
        return 0;
    }
    return t->done;
}

/* `task.cancel()`: ask the task to stop. The cancel lands on the task's next
   step: a task that is suspended stops there, and a task that has not run yet
   never runs its body. CPython returns True when the cancel landed (the task
   was not already done) and False otherwise. */
int64_t ps_task_cancel(void *task) {
    if (task == NULL) {
        return 0;
    }
    PsTask *t = (PsTask *)task;
    /* Delivered or finished: a cancel does not land again. CPython returns
       True while the request stands (a pending cancel re-requests), False
       once the task is done. */
    if (t->done && t->cancelled != 1) {
        return 0;
    }
    t->cancelled = 1;
    /* The task stops at its next step, so the loop has to look at it again:
       re-queue it right away. The step then finishes it and wakes its
       waiters, which see the cancellation. */
    if (!t->queued) {
        t->queued = 1;
        ps_ready_push(t);
    }
    return 1;
}

/* `task.cancelled()`: True once the cancellation has been delivered. A cancel
   that has not landed yet (the task is still running) reads False, like
   CPython's: the flag only flips when the task actually stops. */
int64_t ps_task_cancelled(void *task) {
    if (task == NULL) {
        return 0;
    }
    return ((PsTask *)task)->cancelled == 2;
}

/* `await task`: park the calling frame until the task completes. The task
   may already be done, in which case the caller does not park at all.
   Return: 1 done, 0 parked, 2 done and failed (ps_task_failure re-raises
   it), 3 the task was cancelled (the caller raises CancelledError instead
   of reading a result). */
int64_t ps_task_await(void *task, void *frame) {
    if (task == NULL) {
        return 1;
    }
    PsTask *t = (PsTask *)task;
    if (t->cancelled) {
        /* Cancelled before it ran, or at a step: the awaiter sees
           CancelledError, the coroutine body never continues. */
        return 3;
    }
    if (t->done) {
        return t->exc != NULL ? 2 : 1;
    }
    ps_task_add_waiter(t, frame);
    return 0;
}

/* Re-raise the failure a task handle holds, in the awaiting frame. The
   awaiter runs this inside its own resume body, so its try outline sees the
   exception exactly where CPython's awaiter would. */
void ps_task_failure(void *task) {
    if (task == NULL) {
        return;
    }
    PsTask *t = (PsTask *)task;
    if (t->exc != NULL) {
        ps_raise_exc(t->exc);
    }
}

/* Read the result the task's coroutine produced. The frame's field 2 is
   `.ps_result` (field 0 `.ps_state`, field 1 `.ps_task` — the layout coro.py
   guarantees), and `size` is the element size the caller's type carries. */
void ps_task_result(void *task, int64_t size, void *out) {
    if (task == NULL || out == NULL || size <= 0) {
        return;
    }
    PsTask *t = (PsTask *)task;
    memcpy(out, (char *)t->frame + 16, (size_t)size);
}

/* --- wait_for (stage D2) -------------------------------------------------- */

/* `asyncio.wait_for(aw, d)`: run the awaitable's task under a deadline. 1
   when the task is already done (the caller reads the result straight away);
   otherwise a deadline row is armed and the awaiting frame parks on the task,
   exactly like `await task`. */
int64_t ps_task_wait_for(void *task, double delay, void *frame) {
    if (task == NULL) {
        return 1;
    }
    PsTask *t = (PsTask *)task;
    if (t->done) {
        return 1;
    }
    ps_deadline_arm(t, frame, delay);
    ps_task_add_waiter(t, frame);
    return 0;
}

/* The outcome the awaiting frame reads on re-entry: 0 the task completed, 1
   the deadline fired first — the task was cancelled and the caller raises
   TimeoutError. Either way the frame's rows are dropped, so a later wait in
   the same frame starts clean. */
int64_t ps_task_wait_outcome(void *frame) {
    int32_t fired = ps_deadline_fired(frame);
    ps_deadline_drop(frame);
    return fired;
}
