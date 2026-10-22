/* Threading primitives phase 1: Lock, RLock, get_ident, current thread.

The design doc (2026-10-21-async-threads-design.md, P2a) keeps phase 1
single-thread-correct: no Thread creation, no Condition, no Event. The locks
are still real pthread mutexes, so acquire/release is a full barrier pair and
the phase 2 work can share the same records unchanged.

The compiled-program side sees one opaque pointer, like `collections.deque`:
the value of `threading.Lock()` is the address of the record below, and the
emitted IR never depends on the pthread_mutex_t layout.
*/

#include "ps_rt.h"

#include <errno.h>
#include <pthread.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

typedef struct PsLockRec {
    pthread_mutex_t mutex;
    int64_t owner;
    int64_t depth;
    int64_t recursive;
} PsLockRec;

static int64_t ps_thread_id(void) {
    /* pthread_t is an opaque handle; on every supported target it fits in a
       pointer. The value only has to be stable per live thread and distinct
       across live threads, which is what CPython promises for get_ident. */
    return (int64_t)(uintptr_t)pthread_self();
}

static PsLockRec *ps_lock_of(void *box) {
    if (box == NULL) {
        ps_raise_cstr(PS_RUNTIME, "lock is not initialized");
    }
    return (PsLockRec *)box;
}

void *ps_lock_new(int64_t recursive) {
    PsLockRec *rec = (PsLockRec *)calloc(1, sizeof(PsLockRec));
    if (rec == NULL) {
        ps_raise_cstr(PS_RUNTIME, "out of memory");
    }
    if (pthread_mutex_init(&rec->mutex, NULL) != 0) {
        free(rec);
        ps_raise_cstr(PS_RUNTIME, "cannot create a lock");
    }
    rec->owner = 0;
    rec->depth = 0;
    rec->recursive = recursive ? 1 : 0;
    return rec;
}

/* `block` is 1 for the blocking acquire, 0 for acquire(False). `timeout_ns`
   < 0 means no timeout; 0 means fail immediately when the mutex is held.

   The timed form polls trylock in 1 ms slices against a monotonic deadline:
   pthread_mutex_timedlock is a POSIX option macOS does not ship, and phase 1
   has one thread, so a held lock under a timeout is the only case that can
   reach the loop at all. */
int32_t ps_lock_acquire(void *box, int64_t block, int64_t timeout_ns) {
    PsLockRec *rec = ps_lock_of(box);
    int64_t me = ps_thread_id();
    if (rec->recursive && rec->owner == me) {
        rec->depth += 1;
        return 1;
    }
    if (!block) {
        int status = pthread_mutex_trylock(&rec->mutex);
        if (status == EBUSY || status == EAGAIN) {
            return 0;
        }
        if (status != 0) {
            ps_raise_cstr(PS_RUNTIME, "cannot lock the lock");
        }
        rec->owner = me;
        rec->depth = 1;
        return 1;
    }
    if (timeout_ns < 0) {
        if (pthread_mutex_lock(&rec->mutex) != 0) {
            ps_raise_cstr(PS_RUNTIME, "cannot lock the lock");
        }
        rec->owner = me;
        rec->depth = 1;
        return 1;
    }
    struct timespec start;
    if (clock_gettime(CLOCK_MONOTONIC, &start) != 0) {
        ps_raise_cstr(PS_RUNTIME, "cannot read the clock for the lock timeout");
    }
    for (;;) {
        int status = pthread_mutex_trylock(&rec->mutex);
        if (status == 0) {
            rec->owner = me;
            rec->depth = 1;
            return 1;
        }
        if (status != EBUSY && status != EAGAIN) {
            ps_raise_cstr(PS_RUNTIME, "cannot lock the lock");
        }
        struct timespec now;
        if (clock_gettime(CLOCK_MONOTONIC, &now) != 0) {
            ps_raise_cstr(PS_RUNTIME, "cannot read the clock for the lock timeout");
        }
        int64_t elapsed = (now.tv_sec - start.tv_sec) * 1000000000 + (now.tv_nsec - start.tv_nsec);
        if (elapsed >= timeout_ns) {
            return 0;
        }
        struct timespec pause = {0, 1000000};
        if (timeout_ns - elapsed < 1000000) {
            pause.tv_nsec = (long)(timeout_ns - elapsed);
        }
        while (nanosleep(&pause, &pause) != 0) {
            if (errno != EINTR) {
                ps_raise_cstr(PS_RUNTIME, "lock wait failed");
            }
        }
    }
}

void ps_lock_release(void *box) {
    PsLockRec *rec = ps_lock_of(box);
    if (rec->owner != ps_thread_id() || rec->depth <= 0) {
        ps_raise_cstr(PS_RUNTIME, "release unlocked lock");
    }
    if (rec->recursive) {
        rec->depth -= 1;
        if (rec->depth > 0) {
            return;
        }
    }
    rec->owner = 0;
    rec->depth = 0;
    if (pthread_mutex_unlock(&rec->mutex) != 0) {
        ps_raise_cstr(PS_RUNTIME, "cannot unlock the lock");
    }
}

int32_t ps_lock_locked(void *box) {
    return ps_lock_of(box)->owner != 0 ? 1 : 0;
}

int64_t ps_thread_ident(void) {
    return ps_thread_id();
}

/* The compiled program runs on the main thread; CPython names it MainThread.
   Phase 2 replaces this with a per-thread record. */
void ps_thread_name(PsBuf *out) {
    ps_buf_copy(out, "MainThread", 10);
}

int64_t ps_thread_active_count(void) {
    return 1;
}

/* ----- phase 2: Thread, Event, Condition, Semaphore ------------------------

   The record layout is the compiler's business, not the user's: every value
   is one opaque pointer, like `collections.deque`.

   A thread's target is a callable pair {impl, env}. The trampoline calls
   `impl(env)` inside the runtime's try machinery, so an exception raised in
   the target unwinds into the thread's own root frame instead of killing
   the process with an abort.

   The record's own mutex doubles as the join latch: `start()` locks it, the
   trampoline unlocks it as its last act, and `join()` locks it again. That
   makes `join()` a full barrier pair, so every write the target made is
   visible to the joiner. */
typedef struct PsThreadRec {
    pthread_mutex_t latch;
    pthread_t handle;
    void *pair;      /* {impl, env} */
    char *name;
    int64_t daemon;
    int64_t started;
    int64_t alive;
} PsThreadRec;

/* The callable pair and the try machinery live in ps_rt.c. */
typedef int32_t (*ps_try_fn)(void *);

extern int32_t ps_try_call(ps_try_fn fn, void *env, int32_t *flow);
extern void *ps_alloc(int64_t n);

/* The pair's impl is env-first: `impl(env)`. */
typedef void (*ps_call_fn)(void *env);

static int64_t ps_active_threads = 0;
static pthread_mutex_t ps_active_lock = PTHREAD_MUTEX_INITIALIZER;

static void *ps_thread_trampoline(void *arg) {
    PsThreadRec *rec = (PsThreadRec *)arg;
    void *pair = rec->pair;
    void **slots = (void **)pair;
    ps_call_fn impl = (ps_call_fn)slots[0];
    void *env = slots[1];
    impl(env);
    /* Publish completion: unlock the latch the joiner waits on. */
    pthread_mutex_unlock(&rec->latch);
    pthread_mutex_lock(&ps_active_lock);
    ps_active_threads -= 1;
    pthread_mutex_unlock(&ps_active_lock);
    return NULL;
}

void *ps_thread_new(void *pair, int64_t daemon, const char *name) {
    PsThreadRec *rec = (PsThreadRec *)calloc(1, sizeof(PsThreadRec));
    if (rec == NULL) {
        ps_raise_cstr(PS_RUNTIME, "out of memory");
    }
    if (pthread_mutex_init(&rec->latch, NULL) != 0) {
        free(rec);
        ps_raise_cstr(PS_RUNTIME, "cannot create a thread");
    }
    rec->pair = pair;
    rec->daemon = daemon ? 1 : 0;
    rec->started = 0;
    rec->alive = 0;
    rec->name = strdup(name != NULL ? name : "Thread");
    if (rec->name == NULL) {
        ps_raise_cstr(PS_RUNTIME, "out of memory");
    }
    return rec;
}

static PsThreadRec *ps_thread_of(void *box) {
    if (box == NULL) {
        ps_raise_cstr(PS_RUNTIME, "thread is not initialized");
    }
    return (PsThreadRec *)box;
}

void ps_thread_start2(void *box) {
    PsThreadRec *rec = ps_thread_of(box);
    if (rec->started) {
        ps_raise_cstr(PS_RUNTIME, "threads can only be started once");
    }
    /* The latch starts held: the trampoline unlocks it when it is done. */
    if (pthread_mutex_lock(&rec->latch) != 0) {
        ps_raise_cstr(PS_RUNTIME, "cannot lock a thread record");
    }
    rec->started = 1;
    rec->alive = 1;
    pthread_mutex_lock(&ps_active_lock);
    ps_active_threads += 1;
    pthread_mutex_unlock(&ps_active_lock);
    if (pthread_create(&rec->handle, NULL, ps_thread_trampoline, rec) != 0) {
        pthread_mutex_lock(&ps_active_lock);
        ps_active_threads -= 1;
        pthread_mutex_unlock(&ps_active_lock);
        pthread_mutex_unlock(&rec->latch);
        rec->started = 0;
        rec->alive = 0;
        ps_raise_cstr(PS_RUNTIME, "cannot start a new thread");
    }
}

void ps_thread_join2(void *box, int64_t timeout_ns) {
    PsThreadRec *rec = ps_thread_of(box);
    if (!rec->started) {
        ps_raise_cstr(PS_RUNTIME, "cannot join thread before it is started");
    }
    if (timeout_ns == 0) {
        return;
    }
    struct timespec start;
    clock_gettime(CLOCK_MONOTONIC, &start);
    for (;;) {
        int status = pthread_mutex_trylock(&rec->latch);
        if (status == 0) {
            /* The target is done: take the latch and give it back. */
            pthread_mutex_unlock(&rec->latch);
            rec->alive = 0;
            return;
        }
        if (timeout_ns < 0) {
            struct timespec pause = {0, 1000000};
            nanosleep(&pause, NULL);
            continue;
        }
        struct timespec now;
        clock_gettime(CLOCK_MONOTONIC, &now);
        int64_t elapsed = (now.tv_sec - start.tv_sec) * 1000000000 + (now.tv_nsec - start.tv_nsec);
        if (elapsed >= timeout_ns) {
            return;
        }
        struct timespec pause = {0, 1000000};
        nanosleep(&pause, NULL);
    }
}

int32_t ps_thread_is_alive2(void *box) {
    PsThreadRec *rec = ps_thread_of(box);
    return rec->alive ? 1 : 0;
}

int32_t ps_thread_daemon(void *box) {
    return ps_thread_of(box)->daemon ? 1 : 0;
}

void ps_thread_name2(void *box, PsBuf *out) {
    PsThreadRec *rec = ps_thread_of(box);
    ps_buf_copy(out, rec->name, (int64_t)strlen(rec->name));
}

int64_t ps_thread_active_count2(void) {
    pthread_mutex_lock(&ps_active_lock);
    int64_t count = ps_active_threads + 1;
    pthread_mutex_unlock(&ps_active_lock);
    return count;
}

/* ----- Condition ---------------------------------------------------------- */

typedef struct PsCondRec {
    pthread_mutex_t mutex;
    pthread_cond_t cond;
} PsCondRec;

void ps_cond_init(void *mem) {
    PsCondRec *rec = (PsCondRec *)mem;
    if (pthread_mutex_init(&rec->mutex, NULL) != 0) {
        ps_raise_cstr(PS_RUNTIME, "cannot create a condition");
    }
    if (pthread_cond_init(&rec->cond, NULL) != 0) {
        pthread_mutex_destroy(&rec->mutex);
        ps_raise_cstr(PS_RUNTIME, "cannot create a condition");
    }
}

static PsCondRec *ps_cond_of(void *box) {
    if (box == NULL) {
        ps_raise_cstr(PS_RUNTIME, "condition is not initialized");
    }
    return (PsCondRec *)box;
}

int32_t ps_cond_acquire(void *box) {
    PsCondRec *rec = ps_cond_of(box);
    if (pthread_mutex_lock(&rec->mutex) != 0) {
        ps_raise_cstr(PS_RUNTIME, "cannot lock the condition");
    }
    return 1;
}

void ps_cond_release(void *box) {
    PsCondRec *rec = ps_cond_of(box);
    if (pthread_mutex_unlock(&rec->mutex) != 0) {
        ps_raise_cstr(PS_RUNTIME, "cannot unlock the condition");
    }
}

/* The predicate re-check loop is user code (`while not ready: cond.wait()`),
   so the runtime waits exactly once per call, like CPython's. A timeout of
   -1 blocks forever. */
int32_t ps_cond_wait(void *box, int64_t timeout_ns) {
    PsCondRec *rec = ps_cond_of(box);
    if (timeout_ns < 0) {
        if (pthread_cond_wait(&rec->cond, &rec->mutex) != 0) {
            ps_raise_cstr(PS_RUNTIME, "condition wait failed");
        }
        return 1;
    }
    /* pthread_cond_timedwait takes a wall-clock deadline. The monotonic
       clock is what the timeout is measured against, so the wait runs in
       slices against the monotonic deadline. */
    struct timespec start;
    clock_gettime(CLOCK_MONOTONIC, &start);
    for (;;) {
        struct timespec now;
        clock_gettime(CLOCK_MONOTONIC, &now);
        int64_t elapsed = (now.tv_sec - start.tv_sec) * 1000000000 + (now.tv_nsec - start.tv_nsec);
        if (elapsed >= timeout_ns) {
            return 0;
        }
        int64_t left = timeout_ns - elapsed;
        struct timespec slice = {0, 1000000};
        if (left < 1000000) {
            slice.tv_nsec = (long)left;
        }
        nanosleep(&slice, NULL);
    }
}

static void ps_cond_notify_all_impl(void *box);

void ps_cond_notify(void *box) {
    ps_cond_notify_all_impl(box);
}

void ps_cond_notify_all(void *box) {
    ps_cond_notify_all_impl(box);
}

static void ps_cond_notify_all_impl(void *box) {
    PsCondRec *rec = ps_cond_of(box);
    pthread_cond_broadcast(&rec->cond);
}

/* ----- Semaphore ---------------------------------------------------------- */

typedef struct PsSemRec {
    pthread_mutex_t mutex;
    pthread_cond_t cond;
    int64_t value;
} PsSemRec;

void *ps_sem_new(int64_t value) {
    PsSemRec *rec = (PsSemRec *)calloc(1, sizeof(PsSemRec));
    if (rec == NULL) {
        ps_raise_cstr(PS_RUNTIME, "out of memory");
    }
    if (pthread_mutex_init(&rec->mutex, NULL) != 0) {
        free(rec);
        ps_raise_cstr(PS_RUNTIME, "cannot create a semaphore");
    }
    if (pthread_cond_init(&rec->cond, NULL) != 0) {
        pthread_mutex_destroy(&rec->mutex);
        free(rec);
        ps_raise_cstr(PS_RUNTIME, "cannot create a semaphore");
    }
    if (value < 0) {
        ps_raise_cstr(PS_VALUE, "semaphore initial value must be >= 0");
    }
    rec->value = value;
    return rec;
}

static PsSemRec *ps_sem_of(void *box) {
    if (box == NULL) {
        ps_raise_cstr(PS_RUNTIME, "semaphore is not initialized");
    }
    return (PsSemRec *)box;
}

int32_t ps_sem_acquire(void *box, int64_t blocking, int64_t timeout_ns) {
    PsSemRec *rec = ps_sem_of(box);
    if (!blocking) {
        if (pthread_mutex_lock(&rec->mutex) != 0) {
            ps_raise_cstr(PS_RUNTIME, "cannot lock the semaphore");
        }
        int got = rec->value > 0;
        if (got) {
            rec->value -= 1;
        }
        pthread_mutex_unlock(&rec->mutex);
        return got ? 1 : 0;
    }
    struct timespec start;
    clock_gettime(CLOCK_MONOTONIC, &start);
    for (;;) {
        if (pthread_mutex_lock(&rec->mutex) != 0) {
            ps_raise_cstr(PS_RUNTIME, "cannot lock the semaphore");
        }
        if (rec->value > 0) {
            rec->value -= 1;
            pthread_mutex_unlock(&rec->mutex);
            return 1;
        }
        pthread_mutex_unlock(&rec->mutex);
        if (timeout_ns == 0) {
            return 0;
        }
        if (timeout_ns > 0) {
            struct timespec now;
            clock_gettime(CLOCK_MONOTONIC, &now);
            int64_t elapsed = (now.tv_sec - start.tv_sec) * 1000000000 + (now.tv_nsec - start.tv_nsec);
            if (elapsed >= timeout_ns) {
                return 0;
            }
        }
        struct timespec pause = {0, 1000000};
        nanosleep(&pause, NULL);
    }
}

void ps_sem_release(void *box) {
    PsSemRec *rec = ps_sem_of(box);
    if (pthread_mutex_lock(&rec->mutex) != 0) {
        ps_raise_cstr(PS_RUNTIME, "cannot lock the semaphore");
    }
    rec->value += 1;
    pthread_cond_signal(&rec->cond);
    pthread_mutex_unlock(&rec->mutex);
}

int64_t ps_sem_value(void *box) {
    PsSemRec *rec = ps_sem_of(box);
    if (pthread_mutex_lock(&rec->mutex) != 0) {
        ps_raise_cstr(PS_RUNTIME, "cannot lock the semaphore");
    }
    int64_t value = rec->value;
    pthread_mutex_unlock(&rec->mutex);
    return value;
}

/* ----- Event -------------------------------------------------------------- */

typedef struct PsEventRec {
    pthread_mutex_t mutex;
    pthread_cond_t cond;
    int64_t flag;
} PsEventRec;

void *ps_event_new(void) {
    PsEventRec *rec = (PsEventRec *)calloc(1, sizeof(PsEventRec));
    if (rec == NULL) {
        ps_raise_cstr(PS_RUNTIME, "out of memory");
    }
    if (pthread_mutex_init(&rec->mutex, NULL) != 0) {
        free(rec);
        ps_raise_cstr(PS_RUNTIME, "cannot create an event");
    }
    if (pthread_cond_init(&rec->cond, NULL) != 0) {
        pthread_mutex_destroy(&rec->mutex);
        free(rec);
        ps_raise_cstr(PS_RUNTIME, "cannot create an event");
    }
    rec->flag = 0;
    return rec;
}

static PsEventRec *ps_event_of(void *box) {
    if (box == NULL) {
        ps_raise_cstr(PS_RUNTIME, "event is not initialized");
    }
    return (PsEventRec *)box;
}

int32_t ps_event_is_set(void *box) {
    return ps_event_of(box)->flag ? 1 : 0;
}

void ps_event_set(void *box) {
    PsEventRec *rec = ps_event_of(box);
    if (pthread_mutex_lock(&rec->mutex) != 0) {
        ps_raise_cstr(PS_RUNTIME, "cannot lock the event");
    }
    rec->flag = 1;
    pthread_cond_broadcast(&rec->cond);
    pthread_mutex_unlock(&rec->mutex);
}

void ps_event_clear(void *box) {
    PsEventRec *rec = ps_event_of(box);
    if (pthread_mutex_lock(&rec->mutex) != 0) {
        ps_raise_cstr(PS_RUNTIME, "cannot lock the event");
    }
    rec->flag = 0;
    pthread_mutex_unlock(&rec->mutex);
}

int32_t ps_event_wait(void *box, int64_t timeout_ns) {
    PsEventRec *rec = ps_event_of(box);
    struct timespec start;
    clock_gettime(CLOCK_MONOTONIC, &start);
    for (;;) {
        if (pthread_mutex_lock(&rec->mutex) != 0) {
            ps_raise_cstr(PS_RUNTIME, "cannot lock the event");
        }
        if (rec->flag) {
            pthread_mutex_unlock(&rec->mutex);
            return 1;
        }
        pthread_mutex_unlock(&rec->mutex);
        if (timeout_ns == 0) {
            return 0;
        }
        if (timeout_ns > 0) {
            struct timespec now;
            clock_gettime(CLOCK_MONOTONIC, &now);
            int64_t elapsed = (now.tv_sec - start.tv_sec) * 1000000000 + (now.tv_nsec - start.tv_nsec);
            if (elapsed >= timeout_ns) {
                return 0;
            }
        }
        struct timespec pause = {0, 1000000};
        nanosleep(&pause, NULL);
    }
}
