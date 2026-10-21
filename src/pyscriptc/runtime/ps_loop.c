/* The single-threaded run-to-completion loop behind `asyncio.run`, sleep and
gather.

A coroutine frame is a heap object the compiler builds (coro.py). The loop
never names its layout beyond two fixed slots the IR pass guarantees on every
coroutine frame: field 0 is `.ps_state` (i64) and field 1 is `.ps_task`, a
callable cell `{ impl, env }` whose implementation is the frame's env-first
resume shim `coro.<name>.resume.clo(env, frame) -> i32`. Status 0 means the
coroutine is pending, 1 means done — the same numbers the guard transform
uses.

`asyncio.sleep(d)` arms a timer row keyed by frame; the loop re-queues the
frame when the deadline passes. `asyncio.gather` registers several frames and
drives them together, so the children interleave exactly like CPython's.
*/

#include "ps_rt.h"

#include <stdlib.h>
#include <time.h>

/* Offsets into every coroutine frame (coro.py guarantees this layout). */
#define PS_TASK_OFF 8

typedef struct PsTask PsTask;

struct PsTask {
    int64_t (*impl)(void *, void *);
    void *env;
    void *frame;
    PsTask *next;
    int32_t done;
};

typedef struct PsTimer {
    void *frame;
    double deadline;
} PsTimer;

static PsTask *ps_ready_head;
static PsTask *ps_ready_tail;
static PsTimer *ps_timers;
static int64_t ps_timer_len;
static int64_t ps_timer_cap;

void ps_loop_reset(void) {
    ps_ready_head = ps_ready_tail = NULL;
    ps_timer_len = 0;
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

/* Run one ready task once. 1 when the task finished, 0 while pending. */
static int64_t ps_task_step(PsTask *task) {
    int64_t status = task->impl(task->env, task->frame);
    if (status == 1) {
        task->done = 1;
    }
    return status;
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
    ps_timer_len++;
}

/* Move every due timer's frame back onto the ready queue. */
static int32_t ps_fire_due(void) {
    double now = ps_now();
    int64_t kept = 0;
    int32_t fired = 0;
    for (int64_t i = 0; i < ps_timer_len; i++) {
        if (ps_timers[i].deadline <= now) {
            PsTask *task = malloc(sizeof(PsTask));
            if (task == NULL) {
                abort();
            }
            ps_task_of(ps_timers[i].frame, &task->impl, &task->env);
            task->frame = ps_timers[i].frame;
            task->done = 0;
            ps_ready_push(task);
            fired = 1;
            continue;
        }
        ps_timers[kept++] = ps_timers[i];
    }
    ps_timer_len = kept;
    return fired;
}

static void ps_ready_task(void *frame) {
    PsTask *task = malloc(sizeof(PsTask));
    if (task == NULL) {
        abort();
    }
    ps_task_of(frame, &task->impl, &task->env);
    task->frame = frame;
    task->done = 0;
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
            ps_task_step(task);
            free(task);
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
