/* The asyncio primitives: Lock, Event, Semaphore, Queue and to_thread.

The design doc (2026-10-21-async-threads-design.md, P3) scopes the surface,
and the stage D3 breakdown in `.agents/docs/design/async-lowering-plan.md`
splits it. Every value is an opaque pointer to a runtime record, the same
shape the threading records use: the emitted IR never sees the layout.

The wait is cooperative, not blocking. A `threading.Lock` blocks the thread
that waits on it; an `asyncio.Lock` parks the *task* that waits on it, and
the loop re-queues that task when the lock is released. The primitives
therefore hold a waiter list of task pointers, and the loop owns the task
records (ps_loop.c), so the primitives ask the loop to wake a task rather
than running it themselves.

The records live on the loop's thread, so they need no mutex: the only
concurrent writer is `to_thread`, and that one hands its completion over
through a mutex of its own.

`to_thread` runs the callable on a real pthread and delivers the result back
to the loop. The loop does not poll the thread, so the completion is checked
at the top of every loop iteration: the awaiting task is parked until the
worker marks the record done. The honest boundary is that the loop spins
while the thread runs; CPython's `to_thread` lets the loop run other tasks
instead. The divergence is recorded in the stage D3 breakdown.
*/

#include "ps_rt.h"

#include <pthread.h>
#include <stdlib.h>
#include <string.h>

/* The loop's task handle. The primitives only park and wake tasks, so the
   record stays opaque here; ps_loop.c owns the layout. */
extern void *ps_loop_current_task(void);
extern void ps_loop_wake_task(void *task);

/* --- the waiter list ---------------------------------------------------- */

/* A waiter list: the tasks parked on a primitive, in the order they parked.
   CPython's `asyncio.Lock` wakes the first waiter in order, so the list is a
   FIFO. */
typedef struct PsWaiters {
    void **tasks;
    int64_t len;
    int64_t cap;
} PsWaiters;

static void ps_waiters_push(PsWaiters *waiters, void *task) {
    if (waiters->len == waiters->cap) {
        int64_t cap = waiters->cap == 0 ? 4 : waiters->cap * 2;
        void **grown = realloc(waiters->tasks, (size_t)cap * sizeof(void *));
        if (grown == NULL) {
            abort();
        }
        waiters->tasks = grown;
        waiters->cap = cap;
    }
    waiters->tasks[waiters->len++] = task;
}

/* Take the first waiter off the list and wake it. Returns 0 when the list
   was empty. */
static int64_t ps_waiters_wake_one(PsWaiters *waiters) {
    if (waiters->len == 0) {
        return 0;
    }
    void *task = waiters->tasks[0];
    int64_t kept = waiters->len - 1;
    for (int64_t i = 0; i < kept; i++) {
        waiters->tasks[i] = waiters->tasks[i + 1];
    }
    waiters->len = kept;
    ps_loop_wake_task(task);
    return 1;
}

static void ps_waiters_wake_all(PsWaiters *waiters) {
    while (ps_waiters_wake_one(waiters)) {
        /* Wake every waiter. */
    }
}

/* The task that runs right now, or NULL when the call is not inside a
   coroutine. The cooperative waits park it. */
static void *ps_running_task(void) {
    void *task = ps_loop_current_task();
    if (task == NULL) {
        ps_raise_cstr(PS_RUNTIME, "the asyncio primitives are only usable inside a coroutine");
    }
    return task;
}

/* --- Lock ---------------------------------------------------------------- */

typedef struct PsALock {
    int32_t locked;
    PsWaiters waiters;
} PsALock;

void *ps_alock_new(void) {
    PsALock *lock = calloc(1, sizeof(PsALock));
    if (lock == NULL) {
        ps_raise_cstr(PS_RUNTIME, "out of memory");
    }
    return lock;
}

/* `await lock.acquire()`: take the lock, or park the running task on it.
   Returns 1 when the lock was taken (the caller does not park) and 0 when
   the caller has to wait. The parked task is woken by `release`. */
int64_t ps_alock_acquire(void *box) {
    PsALock *lock = (PsALock *)box;
    if (lock == NULL) {
        ps_raise_cstr(PS_RUNTIME, "lock is not initialized");
    }
    if (!lock->locked) {
        lock->locked = 1;
        return 1;
    }
    ps_waiters_push(&lock->waiters, ps_running_task());
    return 0;
}

/* `lock.release()`: hand the lock to the first waiter, or free it. */
void ps_alock_release(void *box) {
    PsALock *lock = (PsALock *)box;
    if (lock == NULL) {
        ps_raise_cstr(PS_RUNTIME, "lock is not initialized");
    }
    if (!lock->locked) {
        ps_raise_cstr(PS_RUNTIME, "Lock is not acquired");
    }
    if (ps_waiters_wake_one(&lock->waiters)) {
        /* The woken waiter owns the lock now: it takes it on the resume. */
        return;
    }
    lock->locked = 0;
}

int32_t ps_alock_locked(void *box) {
    PsALock *lock = (PsALock *)box;
    if (lock == NULL) {
        ps_raise_cstr(PS_RUNTIME, "lock is not initialized");
    }
    return lock->locked ? 1 : 0;
}

/* --- Event --------------------------------------------------------------- */

typedef struct PsAEvent {
    int32_t set;
    PsWaiters waiters;
} PsAEvent;

void *ps_aevent_new(void) {
    PsAEvent *event = calloc(1, sizeof(PsAEvent));
    if (event == NULL) {
        ps_raise_cstr(PS_RUNTIME, "out of memory");
    }
    return event;
}

/* `await event.wait()`: wait for the event to be set. Returns 1 when the
   event is already set (the caller does not park) and 0 when the caller has
   to wait. `set` wakes every waiter. */
int64_t ps_aevent_wait(void *box) {
    PsAEvent *event = (PsAEvent *)box;
    if (event == NULL) {
        ps_raise_cstr(PS_RUNTIME, "event is not initialized");
    }
    if (event->set) {
        return 1;
    }
    ps_waiters_push(&event->waiters, ps_running_task());
    return 0;
}

void ps_aevent_set(void *box) {
    PsAEvent *event = (PsAEvent *)box;
    if (event == NULL) {
        ps_raise_cstr(PS_RUNTIME, "event is not initialized");
    }
    event->set = 1;
    ps_waiters_wake_all(&event->waiters);
}

void ps_aevent_clear(void *box) {
    PsAEvent *event = (PsAEvent *)box;
    if (event == NULL) {
        ps_raise_cstr(PS_RUNTIME, "event is not initialized");
    }
    event->set = 0;
}

int32_t ps_aevent_is_set(void *box) {
    PsAEvent *event = (PsAEvent *)box;
    if (event == NULL) {
        ps_raise_cstr(PS_RUNTIME, "event is not initialized");
    }
    return event->set ? 1 : 0;
}

/* --- Semaphore ----------------------------------------------------------- */

typedef struct PsASem {
    int64_t value;
    PsWaiters waiters;
} PsASem;

void *ps_asem_new(int64_t value) {
    if (value < 0) {
        ps_raise_cstr(PS_VALUE, "semaphore initial value must be >= 0");
    }
    PsASem *sem = calloc(1, sizeof(PsASem));
    if (sem == NULL) {
        ps_raise_cstr(PS_RUNTIME, "out of memory");
    }
    sem->value = value;
    return sem;
}

/* `await sem.acquire()`: take a permit, or park the running task. Returns 1
   when a permit was taken and 0 when the caller has to wait. `release` hands
   a permit to the first waiter. */
int64_t ps_asem_acquire(void *box) {
    PsASem *sem = (PsASem *)box;
    if (sem == NULL) {
        ps_raise_cstr(PS_RUNTIME, "semaphore is not initialized");
    }
    if (sem->value > 0) {
        sem->value -= 1;
        return 1;
    }
    ps_waiters_push(&sem->waiters, ps_running_task());
    return 0;
}

void ps_asem_release(void *box) {
    PsASem *sem = (PsASem *)box;
    if (sem == NULL) {
        ps_raise_cstr(PS_RUNTIME, "semaphore is not initialized");
    }
    if (ps_waiters_wake_one(&sem->waiters)) {
        /* The woken waiter takes the permit on the resume. */
        return;
    }
    sem->value += 1;
}

int64_t ps_asem_value(void *box) {
    PsASem *sem = (PsASem *)box;
    if (sem == NULL) {
        ps_raise_cstr(PS_RUNTIME, "semaphore is not initialized");
    }
    return sem->value;
}

/* --- Queue --------------------------------------------------------------- */

typedef struct PsAQueue {
    char *data;      /* element slots */
    int64_t elem;    /* element size in bytes */
    int64_t cap;     /* slots allocated */
    int64_t head;    /* offset of the oldest element */
    int64_t len;     /* elements held */
    int64_t maxsize; /* 0 means unbounded */
    /* The element the last `take` removed. The caller reads it out right
       after the take, in the same resume, so one slot is enough. */
    char *taken;
    PsWaiters getters;
    PsWaiters putters;
} PsAQueue;

/* `Queue(maxsize=0)`: the bound and the element size are lowering-time
   constants, unbounded by default, the same shape `queue.Queue` uses. */
void *ps_aqueue_new(int64_t maxsize, int64_t elem) {
    if (elem <= 0) {
        ps_raise_code(PS_VALUE);
    }
    if (maxsize < 0) {
        ps_raise_cstr(PS_VALUE, "maxsize must be >= 0");
    }
    PsAQueue *queue = calloc(1, sizeof(PsAQueue));
    if (queue == NULL) {
        ps_raise_cstr(PS_RUNTIME, "out of memory");
    }
    queue->elem = elem;
    queue->maxsize = maxsize;
    queue->taken = calloc(1, (size_t)elem);
    if (queue->taken == NULL) {
        abort();
    }
    return queue;
}

static char *ps_aqueue_slot(PsAQueue *queue, int64_t i) {
    return queue->data + (size_t)(queue->head + i) * (size_t)queue->elem;
}

/* Make room for one more element at the end of the window. */
static void ps_aqueue_reserve(PsAQueue *queue) {
    if (queue->head + queue->len < queue->cap) {
        return;
    }
    if (queue->len == queue->cap) {
        int64_t cap = queue->cap == 0 ? 4 : queue->cap * 2;
        char *data = calloc((size_t)cap, (size_t)queue->elem);
        if (data == NULL) {
            abort();
        }
        memcpy(data, queue->data + (size_t)queue->head * (size_t)queue->elem, (size_t)queue->len * (size_t)queue->elem);
        free(queue->data);
        queue->data = data;
        queue->cap = cap;
        queue->head = 0;
        return;
    }
    /* The window reached the end of the array: compact it to the front. */
    memmove(queue->data, queue->data + (size_t)queue->head * (size_t)queue->elem, (size_t)queue->len * (size_t)queue->elem);
    queue->head = 0;
}

/* `await queue.put(item)`: the item travels as a pointer to a stashed slot,
   the shape `deque.append` uses. A full queue parks the running task; a
   `get` hands the slot to the first parked putter. */
int64_t ps_aqueue_put(void *box, void *src) {
    PsAQueue *queue = (PsAQueue *)box;
    if (queue == NULL) {
        ps_raise_cstr(PS_RUNTIME, "queue is not initialized");
    }
    if (queue->maxsize > 0 && queue->len >= queue->maxsize) {
        ps_waiters_push(&queue->putters, ps_running_task());
        return 0;
    }
    ps_aqueue_reserve(queue);
    memcpy(ps_aqueue_slot(queue, queue->len), src, (size_t)queue->elem);
    queue->len += 1;
    ps_waiters_wake_one(&queue->getters);
    return 1;
}

/* `await queue.get()`: take the oldest element, or park the running task.
   Returns 1 when an element was taken (it sits in the record's `taken`
   slot until the caller reads it out) and 0 when the caller has to wait. */
int64_t ps_aqueue_take(void *box) {
    PsAQueue *queue = (PsAQueue *)box;
    if (queue == NULL) {
        ps_raise_cstr(PS_RUNTIME, "queue is not initialized");
    }
    if (queue->len == 0) {
        ps_waiters_push(&queue->getters, ps_running_task());
        return 0;
    }
    memcpy(queue->taken, ps_aqueue_slot(queue, 0), (size_t)queue->elem);
    queue->head += 1;
    queue->len -= 1;
    if (queue->len == 0) {
        queue->head = 0;
    }
    ps_waiters_wake_one(&queue->putters);
    return 1;
}

/* Read the element the last `take` removed. */
void ps_aqueue_taken(void *box, int64_t size, void *out) {
    PsAQueue *queue = (PsAQueue *)box;
    if (queue == NULL || out == NULL || size <= 0) {
        return;
    }
    memcpy(out, queue->taken, (size_t)size);
}

int64_t ps_aqueue_qsize(void *box) {
    PsAQueue *queue = (PsAQueue *)box;
    if (queue == NULL) {
        ps_raise_cstr(PS_RUNTIME, "queue is not initialized");
    }
    return queue->len;
}

int32_t ps_aqueue_empty(void *box) {
    return ps_aqueue_qsize(box) == 0 ? 1 : 0;
}

int32_t ps_aqueue_full(void *box) {
    PsAQueue *queue = (PsAQueue *)box;
    if (queue == NULL) {
        ps_raise_cstr(PS_RUNTIME, "queue is not initialized");
    }
    return (queue->maxsize > 0 && queue->len >= queue->maxsize) ? 1 : 0;
}

/* --- to_thread ------------------------------------------------------------ */

/* The record a `to_thread` call owns. The worker writes the result into
   `result` and marks the record done; the loop re-queues the parked task
   when it sees the completion. */
typedef struct PsThreadCall {
    void *pair;      /* the callable pair: {impl, env} */
    void *result;    /* the result slot, owned by the record */
    int64_t size;    /* the result size in bytes */
    int32_t done;    /* set by the worker when the call returned */
    /* The task that parked on this call. The loop wakes it when the
       worker marks the record done. */
    void *waiter;
    pthread_mutex_t lock;
    struct PsThreadCall *next;
} PsThreadCall;

/* Every live thread call, so the loop can poll them. The list is only
   touched by the loop's thread (the worker never joins it), so it needs no
   lock of its own. */
static PsThreadCall *ps_thread_calls;

/* The worker writes the result and marks the record done. The loop polls
   the record, so the worker never touches the loop's queue. */
static void *ps_to_thread_body(void *arg);

/* The callable pair's impl is env-first: `impl(env)`. */
typedef void (*ps_call_fn)(void *env);

/* The body the worker runs: the pair's impl with the pair's env. */
static void *ps_to_thread_body(void *arg) {
    PsThreadCall *call = (PsThreadCall *)arg;
    void **slots = (void **)call->pair;
    ps_call_fn impl = (ps_call_fn)slots[0];
    pthread_detach(pthread_self());
    impl(slots[1]);
    pthread_mutex_lock(&call->lock);
    call->done = 1;
    pthread_mutex_unlock(&call->lock);
    return NULL;
}

/* `asyncio.to_thread(fn, ...)`: the record the call owns. The callable
   pair rides along: the worker runs it and stores the result. */
void *ps_to_thread_new(int64_t size) {
    if (size < 0) {
        ps_raise_code(PS_VALUE);
    }
    PsThreadCall *call = calloc(1, sizeof(PsThreadCall));
    if (call == NULL) {
        ps_raise_cstr(PS_RUNTIME, "out of memory");
    }
    if (pthread_mutex_init(&call->lock, NULL) != 0) {
        free(call);
        ps_raise_cstr(PS_RUNTIME, "cannot create a thread call");
    }
    call->size = size;
    call->result = calloc(1, (size_t)(size > 0 ? size : 1));
    if (call->result == NULL) {
        abort();
    }
    call->next = ps_thread_calls;
    ps_thread_calls = call;
    return call;
}

/* Attach the callable pair and start the worker. */
void ps_to_thread_start(void *box, void *pair) {
    PsThreadCall *call = (PsThreadCall *)box;
    if (call == NULL) {
        ps_raise_cstr(PS_RUNTIME, "thread call is not initialized");
    }
    call->pair = pair;
    pthread_t handle;
    if (pthread_create(&handle, NULL, ps_to_thread_body, call) != 0) {
        ps_raise_cstr(PS_RUNTIME, "cannot start a thread");
    }
}

/* The loop polls the live calls: a call the worker finished wakes the task
   that parked on it. The loop calls this between iterations, so the wakeup
   lands before the next drain. */
void ps_to_thread_pump(void) {    PsThreadCall *call = ps_thread_calls;
    while (call != NULL) {
        if (!call->done && call->waiter != NULL) {
            pthread_mutex_lock(&call->lock);
            int64_t done = call->done ? 1 : 0;
            pthread_mutex_unlock(&call->lock);
            if (done) {
                void *waiter = call->waiter;
                call->waiter = NULL;
                ps_loop_wake_task(waiter);
            }
        }
        call = call->next;
    }
}

/* Park the running task on a thread call: the loop wakes it when the
   worker marks the record done. */
void ps_to_thread_park(void *box) {
    PsThreadCall *call = (PsThreadCall *)box;
    if (call == NULL) {
        return;
    }
    call->waiter = ps_running_task();
}

/* True while a worker is still running: the loop keeps spinning until the
   last one finishes. */
int32_t ps_threads_pending(void) {
    PsThreadCall *call = ps_thread_calls;
    while (call != NULL) {
        if (!call->done) {
            return 1;
        }
        call = call->next;
    }
    return 0;
}

/* The worker stored the result: copy it into the record's slot. The stub
   calls this once the callable returned. */
void ps_to_thread_store(void *box, void *src, int64_t size) {
    PsThreadCall *call = (PsThreadCall *)box;
    if (call == NULL) {
        return;
    }
    pthread_mutex_lock(&call->lock);
    if (src != NULL && size > 0) {
        memcpy(call->result, src, (size_t)size);
    }
    pthread_mutex_unlock(&call->lock);
}

/* The outcome of a `to_thread` call: 0 the worker is still running (the
   caller stays parked), 1 the call returned. */
int64_t ps_to_thread_poll(void *box) {
    PsThreadCall *call = (PsThreadCall *)box;
    if (call == NULL) {
        return 1;
    }
    pthread_mutex_lock(&call->lock);
    int64_t done = call->done ? 1 : 0;
    pthread_mutex_unlock(&call->lock);
    return done;
}

/* Read the result the worker produced. */
void ps_to_thread_result(void *box, int64_t size, void *out) {
    PsThreadCall *call = (PsThreadCall *)box;
    if (call == NULL || out == NULL || size <= 0) {
        return;
    }
    pthread_mutex_lock(&call->lock);
    memcpy(out, call->result, (size_t)size);
    pthread_mutex_unlock(&call->lock);
}
