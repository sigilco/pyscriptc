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
