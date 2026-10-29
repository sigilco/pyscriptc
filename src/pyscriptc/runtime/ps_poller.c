/* The fd poller behind the event loop (#177): kqueue on macOS and the
   BSDs, epoll on Linux, a poll() fallback everywhere else.

   The loop's wakeup sources are timers, thread completions and now file
   descriptors. Timers and completions the loop handles directly; an fd needs
   the kernel to say when it is ready, and this file is that kernel interface
   behind one shape:

     ps_poller_watch(fd, events)      take an interest in an fd
     ps_poller_unwatch(fd, events)    drop that interest
     ps_poller_wait(fds, count, ms)   wait for any of the fds to be ready

   ps_poller_wait blocks up to timeout_ms waiting for any of the fds it was
   handed to become ready and returns the number of ready rows (the rows
   with `ready` set). The loop calls it with the earliest timer deadline as
   the timeout, so a program that waits on nothing still wakes for its
   timers, and a program that waits on a socket does not spin.

   Two kinds of interest live in the same table:

     an explicit hold, from ps_poller_watch / ps_poller_unwatch. The loop
     watches the fds its tasks park on and the selector (#485) watches the
     fds a program registered; both can name the same fd, so the interest
     is counted, not replaced, and the kernel registration lives while a
     count is above zero.

     an implicit hold, from the rows a wait hands over. The rows are the
     caller's current interest, so they are re-asserted on every wait and
     dropped when a later wait does not name them again. That is what keeps
     a closed descriptor from leaving a stale registration behind: a
     descriptor number the process reuses later starts clean.

   The self-pipe: the loop owns one pipe it writes a single byte into from a
   context outside the loop's drive (a signal handler today, a worker thread
   later). The poller registers the pipe's read end on the same handle, so
   an external wakeup interrupts the wait without a signal to the poll call
   itself. `ps_poller_wake()` writes the byte; the poller drains the pipe
   itself after the wait returns.

   Readiness is level-triggered, the way `selectors` and `poll` report it: a
   row is ready when the fd is ready right now. The caller must drain what it
   read; a row that stays ready stays ready.

   The poller is not reentrant and is owned by the loop: one per thread that
   runs a loop, created lazily on the first poll and closed at reset. The
   kernel handle (a kqueue or an epoll set) persists across waits, so a
   registration made on one wait is still live on the next.
*/

#include "ps_rt.h"

#if !defined(__wasi__) && !defined(_WIN32)

#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

#if defined(__APPLE__) || defined(__FreeBSD__) || defined(__OpenBSD__) || defined(__NetBSD__)
#define PS_POLLER_KQUEUE 1
#include <sys/event.h>
#elif defined(__linux__)
#define PS_POLLER_EPOLL 1
#include <sys/epoll.h>
#endif

#include "ps_poller.h"

/* The rows one wait watches. The loop's fd table is small; the cap keeps
   the backend's event array on the stack. */
#define PS_POLL_MAX 65

/* The interest one fd carries: how many explicit holders want reads, how
   many want writes, and whether the last wait named the fd. */
typedef struct {
    int fd;
    int reads;
    int writes;
    int implicit;
} PsWatch;

/* The self-pipe. Both ends are non-blocking: the write end so a wakeup that
   races a full pipe cannot block the writer, the read end so draining the
   pipe never blocks when the pipe runs dry. */
static int ps_pipe_read = -1;
static int ps_pipe_write = -1;

static PsWatch ps_watches[PS_POLL_MAX];
static int ps_nwatches = 0;

static void ps_pipe_init(void) {
    if (ps_pipe_read >= 0) {
        return;
    }
    int fds[2];
    if (pipe(fds) != 0) {
        return;
    }
    for (int i = 0; i < 2; i++) {
        int flags = fcntl(fds[i], F_GETFL, 0);
        if (flags >= 0) {
            (void)fcntl(fds[i], F_SETFL, flags | O_NONBLOCK);
        }
    }
    ps_pipe_read = fds[0];
    ps_pipe_write = fds[1];
}

void ps_poller_wake(void) {
    ps_pipe_init();
    if (ps_pipe_write < 0) {
        return;
    }
    ssize_t wrote = write(ps_pipe_write, "w", 1);
    (void)wrote; /* EAGAIN (a byte already waiting) is a fine outcome */
}

int ps_poller_pipe_fd(void) {
    ps_pipe_init();
    return ps_pipe_read;
}

/* Drain the self-pipe after a wait that reported it ready. The read end is
   non-blocking, so the loop ends when the pipe runs dry instead of
   blocking on a second read. */
static void ps_pipe_drain(void) {
    if (ps_pipe_read < 0) {
        return;
    }
    char buffer[64];
    for (;;) {
        ssize_t got = read(ps_pipe_read, buffer, sizeof buffer);
        if (got <= 0) {
            break;
        }
    }
}

static PsWatch *ps_watch_find(int fd) {
    for (int i = 0; i < ps_nwatches; i++) {
        if (ps_watches[i].fd == fd) {
            return &ps_watches[i];
        }
    }
    return NULL;
}

/* Drop the entry at `index` from the table. The caller has already turned
   the kernel registration off. */
static void ps_watch_drop(int index) {
    ps_watches[index] = ps_watches[ps_nwatches - 1];
    ps_nwatches--;
}

/* --- the backends ------------------------------------------------------- */

#if defined(PS_POLLER_KQUEUE)

/* The kqueue lives across waits; a registration made on one wait is still
   live on the next. */
static int ps_kq = -1;

/* The kernel registration for one watch: which filters are live. */
static int ps_kq_read_on[PS_POLL_MAX];
static int ps_kq_write_on[PS_POLL_MAX];

static int ps_kq_open(void) {
    if (ps_kq < 0) {
        ps_kq = kqueue();
    }
    return ps_kq;
}

static void ps_kq_close(void) {
    if (ps_kq >= 0) {
        close(ps_kq);
        ps_kq = -1;
    }
}

/* The self-pipe's read end is registered for reads on the shared handle,
   once. The flag resets with the pipe, so a fresh pipe is registered
   again. */
static int ps_pipe_registered = 0;

static void ps_kq_pipe_apply(void) {
    if (ps_kq < 0 || ps_pipe_read < 0 || ps_pipe_registered) {
        return;
    }
    struct kevent change;
    EV_SET(&change, ps_pipe_read, EVFILT_READ, EV_ADD, 0, 0, NULL);
    (void)kevent(ps_kq, &change, 1, NULL, 0, NULL);
    ps_pipe_registered = 1;
}

/* Set the kernel registration for one watch. EV_ADD is idempotent, so a
   re-assert costs one syscall and repairs a registration the kernel
   forgot (a descriptor number the process reused). */
static void ps_kq_apply(int index, int fd, int want_read, int want_write) {
    if (ps_kq < 0) {
        return;
    }
    struct kevent changes[2];
    int n = 0;
    if (want_read) {
        EV_SET(&changes[n++], fd, EVFILT_READ, EV_ADD, 0, 0, NULL);
    } else if (ps_kq_read_on[index]) {
        EV_SET(&changes[n++], fd, EVFILT_READ, EV_DELETE, 0, 0, NULL);
    }
    if (want_write) {
        EV_SET(&changes[n++], fd, EVFILT_WRITE, EV_ADD, 0, 0, NULL);
    } else if (ps_kq_write_on[index]) {
        EV_SET(&changes[n++], fd, EVFILT_WRITE, EV_DELETE, 0, 0, NULL);
    }
    if (n > 0) {
        (void)kevent(ps_kq, changes, n, NULL, 0, NULL);
    }
    ps_kq_read_on[index] = want_read;
    ps_kq_write_on[index] = want_write;
}

void ps_poller_watch(int fd, int events) {
    ps_pipe_init();
    (void)ps_kq_open();
    PsWatch *watch = ps_watch_find(fd);
    if (watch == NULL) {
        if (ps_nwatches >= PS_POLL_MAX) {
            return;
        }
        watch = &ps_watches[ps_nwatches];
        watch->fd = fd;
        watch->reads = 0;
        watch->writes = 0;
        watch->implicit = 0;
        ps_kq_read_on[ps_nwatches] = 0;
        ps_kq_write_on[ps_nwatches] = 0;
        ps_nwatches++;
    }
    int index = (int)(watch - ps_watches);
    if (events & POLLIN) {
        watch->reads++;
    }
    if (events & POLLOUT) {
        watch->writes++;
    }
    ps_kq_apply(index, fd, watch->reads > 0, watch->writes > 0);
    ps_kq_pipe_apply();
}

void ps_poller_unwatch(int fd, int events) {
    PsWatch *watch = ps_watch_find(fd);
    if (watch == NULL) {
        return;
    }
    int index = (int)(watch - ps_watches);
    if (events & POLLIN && watch->reads > 0) {
        watch->reads--;
    }
    if (events & POLLOUT && watch->writes > 0) {
        watch->writes--;
    }
    ps_kq_apply(index, fd, watch->reads > 0, watch->writes > 0);
    if (watch->reads == 0 && watch->writes == 0 && !watch->implicit) {
        ps_watch_drop(index);
    }
}

/* The rows a wait hands over are the caller's current interest: they are
   re-asserted on the kernel handle, and an entry the wait does not name
   again loses its implicit hold (and its registration, unless an explicit
   holder still wants it). */
static void ps_kq_sync_rows(PsPollFd *fds, int count) {
    for (int i = 0; i < count; i++) {
        PsWatch *watch = ps_watch_find(fds[i].fd);
        if (watch == NULL) {
            if (ps_nwatches >= PS_POLL_MAX) {
                continue;
            }
            watch = &ps_watches[ps_nwatches];
            watch->fd = fds[i].fd;
            watch->reads = 0;
            watch->writes = 0;
            watch->implicit = 0;
            ps_kq_read_on[ps_nwatches] = 0;
            ps_kq_write_on[ps_nwatches] = 0;
            ps_nwatches++;
        }
        int index = (int)(watch - ps_watches);
        watch->implicit = 1;
        int want_read = watch->reads > 0 || (fds[i].events & POLLIN) != 0;
        ps_kq_apply(index, fds[i].fd, want_read, watch->writes > 0);
    }
    for (int i = 0; i < ps_nwatches;) {
        if (!ps_watches[i].implicit) {
            i++;
            continue;
        }
        int named = 0;
        for (int j = 0; j < count; j++) {
            if (fds[j].fd == ps_watches[i].fd) {
                named = 1;
                break;
            }
        }
        if (named) {
            i++;
            continue;
        }
        ps_watches[i].implicit = 0;
        ps_kq_apply(i, ps_watches[i].fd, ps_watches[i].reads > 0, ps_watches[i].writes > 0);
        if (ps_watches[i].reads == 0 && ps_watches[i].writes == 0) {
            ps_watch_drop(i);
            continue;
        }
        i++;
    }
}

int ps_poller_wait(PsPollFd *fds, int count, int timeout_ms) {
    ps_pipe_init();
    int kq = ps_kq_open();
    if (kq < 0) {
        return -1;
    }
    ps_kq_pipe_apply();
    ps_kq_sync_rows(fds, count);
    for (int i = 0; i < count; i++) {
        fds[i].ready = 0;
    }
    struct timespec timeout;
    struct timespec *when = NULL;
    if (timeout_ms >= 0) {
        timeout.tv_sec = timeout_ms / 1000;
        timeout.tv_nsec = (long)(timeout_ms % 1000) * 1000000L;
        when = &timeout;
    }
    struct kevent events[PS_POLL_MAX];
    int got = kevent(kq, NULL, 0, events, PS_POLL_MAX, when);
    int ready = 0;
    for (int i = 0; i < got; i++) {
        int fd = (int)events[i].ident;
        if (fd == ps_pipe_read) {
            ps_pipe_drain();
            /* The wakeup itself is not a user row: the caller learns of it
               through no row being ready plus a zero return. */
            continue;
        }
        for (int j = 0; j < count; j++) {
            if (fds[j].fd == fd) {
                fds[j].ready = 1;
                ready++;
                break;
            }
        }
    }
    return ready;
}

void ps_poller_reset(void) {
    ps_kq_close();
    ps_nwatches = 0;
    ps_pipe_registered = 0;
    if (ps_pipe_read >= 0) {
        close(ps_pipe_read);
        close(ps_pipe_write);
        ps_pipe_read = ps_pipe_write = -1;
    }
}

#elif defined(PS_POLLER_EPOLL)

static int ps_ep = -1;

static int ps_ep_open(void) {
    if (ps_ep < 0) {
        ps_ep = epoll_create1(0);
    }
    return ps_ep;
}

/* Set the kernel registration for one watch. */
static void ps_ep_apply(int fd, int want_read, int want_write) {
    int ep = ps_ep_open();
    if (ep < 0) {
        return;
    }
    struct epoll_event ev;
    memset(&ev, 0, sizeof ev);
    ev.events = 0;
    if (want_read) {
        ev.events |= EPOLLIN;
    }
    if (want_write) {
        ev.events |= EPOLLOUT;
    }
    ev.data.fd = fd;
    if (epoll_ctl(ep, EPOLL_CTL_ADD, fd, &ev) != 0) {
        (void)epoll_ctl(ep, EPOLL_CTL_MOD, fd, &ev);
    }
}

static void ps_ep_drop(int fd) {
    int ep = ps_ep_open();
    if (ep >= 0) {
        (void)epoll_ctl(ep, EPOLL_CTL_DEL, fd, NULL);
    }
}

void ps_poller_watch(int fd, int events) {
    ps_pipe_init();
    (void)ps_ep_open();
    PsWatch *watch = ps_watch_find(fd);
    if (watch == NULL) {
        if (ps_nwatches >= PS_POLL_MAX) {
            return;
        }
        watch = &ps_watches[ps_nwatches++];
        watch->fd = fd;
        watch->reads = 0;
        watch->writes = 0;
        watch->implicit = 0;
    }
    if (events & POLLIN) {
        watch->reads++;
    }
    if (events & POLLOUT) {
        watch->writes++;
    }
    ps_ep_apply(fd, watch->reads > 0, watch->writes > 0);
    if (ps_pipe_read >= 0) {
        struct epoll_event ev;
        memset(&ev, 0, sizeof ev);
        ev.events = EPOLLIN;
        ev.data.fd = ps_pipe_read;
        (void)epoll_ctl(ps_ep, EPOLL_CTL_ADD, ps_pipe_read, &ev);
    }
}

void ps_poller_unwatch(int fd, int events) {
    PsWatch *watch = ps_watch_find(fd);
    if (watch == NULL) {
        return;
    }
    if (events & POLLIN && watch->reads > 0) {
        watch->reads--;
    }
    if (events & POLLOUT && watch->writes > 0) {
        watch->writes--;
    }
    int index = (int)(watch - ps_watches);
    if (watch->reads == 0 && watch->writes == 0 && !watch->implicit) {
        ps_ep_drop(fd);
        ps_watch_drop(index);
        return;
    }
    ps_ep_apply(fd, watch->reads > 0, watch->writes > 0);
}

static void ps_ep_sync_rows(PsPollFd *fds, int count) {
    for (int i = 0; i < count; i++) {
        PsWatch *watch = ps_watch_find(fds[i].fd);
        if (watch == NULL) {
            if (ps_nwatches >= PS_POLL_MAX) {
                continue;
            }
            watch = &ps_watches[ps_nwatches++];
            watch->fd = fds[i].fd;
            watch->reads = 0;
            watch->writes = 0;
            watch->implicit = 0;
        }
        watch->implicit = 1;
        ps_ep_apply(fds[i].fd, watch->reads > 0 || (fds[i].events & POLLIN) != 0, watch->writes > 0);
    }
    for (int i = 0; i < ps_nwatches;) {
        if (!ps_watches[i].implicit) {
            i++;
            continue;
        }
        int named = 0;
        for (int j = 0; j < count; j++) {
            if (fds[j].fd == ps_watches[i].fd) {
                named = 1;
                break;
            }
        }
        if (named) {
            i++;
            continue;
        }
        ps_watches[i].implicit = 0;
        if (ps_watches[i].reads == 0 && ps_watches[i].writes == 0) {
            ps_ep_drop(ps_watches[i].fd);
            ps_watch_drop(i);
            continue;
        }
        ps_ep_apply(ps_watches[i].fd, ps_watches[i].reads > 0, ps_watches[i].writes > 0);
        i++;
    }
}

int ps_poller_wait(PsPollFd *fds, int count, int timeout_ms) {
    ps_pipe_init();
    int ep = ps_ep_open();
    if (ep < 0) {
        return -1;
    }
    ps_ep_sync_rows(fds, count);
    for (int i = 0; i < count; i++) {
        fds[i].ready = 0;
    }
    struct epoll_event events[PS_POLL_MAX];
    int got = epoll_wait(ep, events, PS_POLL_MAX, timeout_ms);
    int ready = 0;
    for (int i = 0; i < got; i++) {
        int fd = events[i].data.fd;
        if (fd == ps_pipe_read) {
            ps_pipe_drain();
            continue;
        }
        for (int j = 0; j < count; j++) {
            if (fds[j].fd == fd) {
                fds[j].ready = 1;
                ready++;
                break;
            }
        }
    }
    return ready;
}

void ps_poller_reset(void) {
    if (ps_ep >= 0) {
        close(ps_ep);
        ps_ep = -1;
    }
    ps_nwatches = 0;
    if (ps_pipe_read >= 0) {
        close(ps_pipe_read);
        close(ps_pipe_write);
        ps_pipe_read = ps_pipe_write = -1;
    }
}

#else /* poll() fallback */

void ps_poller_watch(int fd, int events) {
    ps_pipe_init();
    PsWatch *watch = ps_watch_find(fd);
    if (watch == NULL) {
        if (ps_nwatches >= PS_POLL_MAX) {
            return;
        }
        watch = &ps_watches[ps_nwatches++];
        watch->fd = fd;
        watch->reads = 0;
        watch->writes = 0;
        watch->implicit = 0;
    }
    if (events & POLLIN) {
        watch->reads++;
    }
    if (events & POLLOUT) {
        watch->writes++;
    }
}

void ps_poller_unwatch(int fd, int events) {
    PsWatch *watch = ps_watch_find(fd);
    if (watch == NULL) {
        return;
    }
    if (events & POLLIN && watch->reads > 0) {
        watch->reads--;
    }
    if (events & POLLOUT && watch->writes > 0) {
        watch->writes--;
    }
    int index = (int)(watch - ps_watches);
    if (watch->reads == 0 && watch->writes == 0 && !watch->implicit) {
        ps_watch_drop(index);
    }
}

int ps_poller_wait(PsPollFd *fds, int count, int timeout_ms) {
    ps_pipe_init();
    struct pollfd pfds[PS_POLL_MAX];
    int n = 0;
    if (ps_pipe_read >= 0 && n < PS_POLL_MAX) {
        pfds[n].fd = ps_pipe_read;
        pfds[n].events = POLLIN;
        pfds[n].revents = 0;
        n++;
    }
    for (int i = 0; i < count && n < PS_POLL_MAX; i++) {
        pfds[n].fd = fds[i].fd;
        pfds[n].events = (short)(fds[i].events & (POLLIN | POLLOUT));
        pfds[n].revents = 0;
        fds[i].ready = 0;
        n++;
    }
    int got = poll(pfds, (nfds_t)n, timeout_ms);
    if (got < 0) {
        return -1;
    }
    int ready = 0;
    for (int i = 0; i < n; i++) {
        if (pfds[i].revents == 0) {
            continue;
        }
        if (pfds[i].fd == ps_pipe_read) {
            ps_pipe_drain();
            continue;
        }
        for (int j = 0; j < count; j++) {
            if (fds[j].fd == pfds[i].fd) {
                fds[j].ready = 1;
                ready++;
                break;
            }
        }
    }
    return ready;
}

void ps_poller_reset(void) {
    ps_nwatches = 0;
    if (ps_pipe_read >= 0) {
        close(ps_pipe_read);
        close(ps_pipe_write);
        ps_pipe_read = ps_pipe_write = -1;
    }
}

#endif /* poller backends */

#else /* __wasi__ || _WIN32 */

/* No poller on a target without sockets: the loop keeps its timer-only
   behavior (the wasm32 lane and the windows lane link the runtime whole,
   so the stubs keep the link whole). */

void ps_poller_watch(int fd, int events) {
    (void)fd;
    (void)events;
}

void ps_poller_unwatch(int fd, int events) {
    (void)fd;
    (void)events;
}

int ps_poller_wait(PsPollFd *fds, int count, int timeout_ms) {
    (void)fds;
    (void)count;
    (void)timeout_ms;
    return 0;
}

void ps_poller_wake(void) {}

int ps_poller_pipe_fd(void) { return -1; }

void ps_poller_reset(void) {}

#endif /* __wasi__ || _WIN32 */
