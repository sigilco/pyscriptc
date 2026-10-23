/* queue.Queue on pthreads (issue #172, Stage C).

The record is one opaque pointer, like `collections.deque` and every
threading record: the value of `queue.Queue()` is this struct's address and
the emitted IR never sees the pthread layout.

The shape follows CPython's queue.py: a bounded FIFO with a mutex, a
not-empty and a not-full condition, and an unfinished-tasks counter that
`task_done` decrements and `join` waits on. LifoQueue shares the record with
a lifo flag; SimpleQueue is the unbounded, join-less variant spelled
`unfinished = -1` here so `task_done` and `join` can refuse it without a
second struct.

The elements live in one byte array. `head` is the offset of the oldest
element and `len` the count, so the elements are the window
`[head, head + len)`. A FIFO pushes at the end of the window and pops from
its front; a LIFO pushes at the front and pops from its end. The window is
compacted to the front of the array when it reaches the end, so the array
never grows past twice the largest queue the program held.

Timeouts are nanoseconds, -1 blocks forever, 0 never waits, matching the
threading primitives in ps_thread.c. The timed waits poll in 1 ms slices
against CLOCK_MONOTONIC, the way `Lock.acquire` does, because
pthread_cond_timedwait takes a wall-clock deadline and the timeout is
measured against the monotonic clock.
*/

#include "ps_rt.h"

#include <pthread.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

typedef struct PsQueueRec {
    pthread_mutex_t mutex;
    pthread_cond_t not_empty;
    pthread_cond_t not_full;
    pthread_cond_t all_tasks_done;
    char *data;      /* element slots */
    int64_t elem;    /* element size in bytes */
    int64_t cap;     /* slots allocated */
    int64_t head;    /* offset of the oldest element */
    int64_t len;     /* elements held */
    int64_t maxsize; /* 0 means unbounded */
    int64_t unfinished;
    int64_t lifo; /* LifoQueue: newest element first */
    /* unfinished < 0: SimpleQueue, no task_done and no join. */
} PsQueueRec;

static PsQueueRec *ps_queue_of(void *box) {
    if (box == NULL) {
        ps_raise_cstr(PS_RUNTIME, "queue is not initialized");
    }
    return (PsQueueRec *)box;
}

static void ps_queue_lock(PsQueueRec *q) {
    if (pthread_mutex_lock(&q->mutex) != 0) {
        ps_raise_cstr(PS_RUNTIME, "cannot lock the queue");
    }
}

static char *ps_queue_slot(PsQueueRec *q, int64_t i) {
    return q->data + (size_t)(q->head + i) * (size_t)q->elem;
}

/* Make room for one more element at the end of the window. */
static void ps_queue_reserve(PsQueueRec *q) {
    if (q->head + q->len < q->cap) {
        return;
    }
    if (q->len == q->cap) {
        /* The queue is full of elements: the array doubles. */
        int64_t cap = q->cap == 0 ? 4 : q->cap * 2;
        char *data = calloc((size_t)cap, (size_t)q->elem);
        if (data == NULL) {
            abort();
        }
        memcpy(data, q->data + (size_t)q->head * (size_t)q->elem, (size_t)q->len * (size_t)q->elem);
        free(q->data);
        q->data = data;
        q->cap = cap;
        q->head = 0;
        return;
    }
    /* The window reached the end of the array: compact it to the front. */
    memmove(q->data, q->data + (size_t)q->head * (size_t)q->elem, (size_t)q->len * (size_t)q->elem);
    q->head = 0;
}

/* `Queue(maxsize=0)`: the bound and the element size are lowering-time
   constants, unbounded by default. `LifoQueue()` shares the record with the
   lifo flag set; `SimpleQueue()` is the join-less variant.
   kind: 0 Queue, 1 LifoQueue, 2 SimpleQueue. */
void *ps_queue_new(int64_t maxsize, int64_t kind, int64_t elem) {
    if (elem <= 0) {
        ps_raise_code(PS_VALUE);
    }
    if (maxsize < 0) {
        ps_raise_cstr(PS_VALUE, "maxsize must be >= 0");
    }
    PsQueueRec *q = calloc(1, sizeof(PsQueueRec));
    if (q == NULL) {
        abort();
    }
    if (pthread_mutex_init(&q->mutex, NULL) != 0) {
        free(q);
        ps_raise_cstr(PS_RUNTIME, "cannot create a queue");
    }
    if (pthread_cond_init(&q->not_empty, NULL) != 0) {
        pthread_mutex_destroy(&q->mutex);
        free(q);
        ps_raise_cstr(PS_RUNTIME, "cannot create a queue");
    }
    if (pthread_cond_init(&q->not_full, NULL) != 0) {
        pthread_cond_destroy(&q->not_empty);
        pthread_mutex_destroy(&q->mutex);
        free(q);
        ps_raise_cstr(PS_RUNTIME, "cannot create a queue");
    }
    if (pthread_cond_init(&q->all_tasks_done, NULL) != 0) {
        pthread_cond_destroy(&q->not_full);
        pthread_cond_destroy(&q->not_empty);
        pthread_mutex_destroy(&q->mutex);
        free(q);
        ps_raise_cstr(PS_RUNTIME, "cannot create a queue");
    }
    /* SimpleQueue is unbounded and carries no unfinished counter: the
       negative value fences task_done and join. */
    q->lifo = kind == 1 ? 1 : 0;
    q->unfinished = kind == 2 ? -1 : 0;
    q->maxsize = maxsize;
    q->elem = elem;
    return q;
}

int64_t ps_queue_qsize(void *box) {
    PsQueueRec *q = ps_queue_of(box);
    ps_queue_lock(q);
    int64_t len = q->len;
    pthread_mutex_unlock(&q->mutex);
    return len;
}

int32_t ps_queue_empty(void *box) {
    return ps_queue_qsize(box) == 0 ? 1 : 0;
}

int32_t ps_queue_full(void *box) {
    PsQueueRec *q = ps_queue_of(box);
    ps_queue_lock(q);
    int32_t full = q->maxsize > 0 && q->len >= q->maxsize;
    pthread_mutex_unlock(&q->mutex);
    return full;
}

/* The element copies under the mutex: a producer wrote it before signalling,
   so the consumer reads it after waking. */
static void ps_queue_take(PsQueueRec *q, void *out) {
    memcpy(out, ps_queue_slot(q, 0), (size_t)q->elem);
    q->head += 1;
    q->len -= 1;
    if (q->len == 0) {
        q->head = 0;
    }
}

/* `timeout_ns` is -1 to block forever and 0 to fail at once: the compiler
   folds a `get_nowait()` and a `get(block=False)` into the 0 timeout. */
void ps_queue_get(void *box, int64_t timeout_ns, void *out) {
    PsQueueRec *q = ps_queue_of(box);
    struct timespec start;
    if (timeout_ns > 0 && clock_gettime(CLOCK_MONOTONIC, &start) != 0) {
        ps_raise_cstr(PS_RUNTIME, "cannot read the clock for the queue timeout");
    }
    ps_queue_lock(q);
    for (;;) {
        if (q->len > 0) {
            if (q->lifo) {
                /* The newest element is the last one in the window. */
                memcpy(out, ps_queue_slot(q, q->len - 1), (size_t)q->elem);
                q->len -= 1;
                if (q->len == 0) {
                    q->head = 0;
                }
            } else {
                ps_queue_take(q, out);
            }
            pthread_cond_signal(&q->not_full);
            pthread_mutex_unlock(&q->mutex);
            return;
        }
        if (timeout_ns == 0) {
            pthread_mutex_unlock(&q->mutex);
            ps_raise_code(PS_QUEUE_EMPTY);
        }
        if (timeout_ns > 0) {
            struct timespec now;
            clock_gettime(CLOCK_MONOTONIC, &now);
            int64_t elapsed = (now.tv_sec - start.tv_sec) * 1000000000 + (now.tv_nsec - start.tv_nsec);
            if (elapsed >= timeout_ns) {
                pthread_mutex_unlock(&q->mutex);
                ps_raise_code(PS_QUEUE_EMPTY);
            }
            struct timespec pause = {0, 1000000};
            int64_t left = timeout_ns - elapsed;
            if (left < 1000000) {
                pause.tv_nsec = (long)left;
            }
            nanosleep(&pause, NULL);
            continue;
        }
        /* Block forever: the real condvar wait, woken by put. */
        if (pthread_cond_wait(&q->not_empty, &q->mutex) != 0) {
            ps_raise_cstr(PS_RUNTIME, "queue wait failed");
        }
    }
}

/* `timeout_ns` is -1 to block forever and 0 to fail at once. */
void ps_queue_put(void *box, int64_t timeout_ns, void *src) {
    PsQueueRec *q = ps_queue_of(box);
    struct timespec start;
    if (timeout_ns > 0 && clock_gettime(CLOCK_MONOTONIC, &start) != 0) {
        ps_raise_cstr(PS_RUNTIME, "cannot read the clock for the queue timeout");
    }
    ps_queue_lock(q);
    for (;;) {
        if (q->maxsize == 0 || q->len < q->maxsize) {
            ps_queue_reserve(q);
            /* Both variants push at the end of the window: a LIFO pops from
               that end too, so the newest element comes first. */
            memcpy(ps_queue_slot(q, q->len), src, (size_t)q->elem);
            q->len += 1;
            if (q->unfinished >= 0) {
                q->unfinished += 1;
            }
            pthread_cond_signal(&q->not_empty);
            pthread_mutex_unlock(&q->mutex);
            return;
        }
        if (timeout_ns == 0) {
            pthread_mutex_unlock(&q->mutex);
            ps_raise_code(PS_QUEUE_FULL);
        }
        if (timeout_ns > 0) {
            struct timespec now;
            clock_gettime(CLOCK_MONOTONIC, &now);
            int64_t elapsed = (now.tv_sec - start.tv_sec) * 1000000000 + (now.tv_nsec - start.tv_nsec);
            if (elapsed >= timeout_ns) {
                pthread_mutex_unlock(&q->mutex);
                ps_raise_code(PS_QUEUE_FULL);
            }
            struct timespec pause = {0, 1000000};
            int64_t left = timeout_ns - elapsed;
            if (left < 1000000) {
                pause.tv_nsec = (long)left;
            }
            nanosleep(&pause, NULL);
            continue;
        }
        if (pthread_cond_wait(&q->not_full, &q->mutex) != 0) {
            ps_raise_cstr(PS_RUNTIME, "queue wait failed");
        }
    }
}

void ps_queue_task_done(void *box) {
    PsQueueRec *q = ps_queue_of(box);
    ps_queue_lock(q);
    if (q->unfinished < 0) {
        pthread_mutex_unlock(&q->mutex);
        ps_raise_cstr(PS_VALUE, "task_done() called on a SimpleQueue");
    }
    if (q->unfinished <= 0) {
        pthread_mutex_unlock(&q->mutex);
        ps_raise_cstr(PS_VALUE, "task_done() called too many times");
    }
    q->unfinished -= 1;
    if (q->unfinished == 0) {
        pthread_cond_broadcast(&q->all_tasks_done);
    }
    pthread_mutex_unlock(&q->mutex);
}

/* `join` waits until every put has its task_done. */
void ps_queue_join(void *box, int64_t timeout_ns) {
    PsQueueRec *q = ps_queue_of(box);
    struct timespec start;
    if (timeout_ns > 0 && clock_gettime(CLOCK_MONOTONIC, &start) != 0) {
        ps_raise_cstr(PS_RUNTIME, "cannot read the clock for the queue timeout");
    }
    ps_queue_lock(q);
    if (q->unfinished < 0) {
        pthread_mutex_unlock(&q->mutex);
        ps_raise_cstr(PS_VALUE, "join() called on a SimpleQueue");
    }
    while (q->unfinished > 0) {
        if (timeout_ns == 0) {
            pthread_mutex_unlock(&q->mutex);
            return;
        }
        if (timeout_ns > 0) {
            struct timespec now;
            clock_gettime(CLOCK_MONOTONIC, &now);
            int64_t elapsed = (now.tv_sec - start.tv_sec) * 1000000000 + (now.tv_nsec - start.tv_nsec);
            if (elapsed >= timeout_ns) {
                pthread_mutex_unlock(&q->mutex);
                return;
            }
            struct timespec pause = {0, 1000000};
            int64_t left = timeout_ns - elapsed;
            if (left < 1000000) {
                pause.tv_nsec = (long)left;
            }
            pthread_mutex_unlock(&q->mutex);
            nanosleep(&pause, NULL);
            ps_queue_lock(q);
            continue;
        }
        if (pthread_cond_wait(&q->all_tasks_done, &q->mutex) != 0) {
            ps_raise_cstr(PS_RUNTIME, "queue wait failed");
        }
    }
    pthread_mutex_unlock(&q->mutex);
}
