/* The fd poller behind the event loop (#177): kqueue on macOS and the
   BSDs, epoll on Linux, a poll() fallback everywhere else.

   The loop's wakeup sources are timers, thread completions and now file
   descriptors. Timers and completions the loop handles directly; an fd needs
   the kernel to say when it is ready, and this file is that kernel interface
   behind one shape:

     ps_poller_poll(fds, fd_count, timeout_ms)

   It blocks up to timeout_ms waiting for any of the fds to become ready and
   returns the number of ready rows (the rows with `ready` set). The loop
   calls it with the earliest timer deadline as the timeout, so a program
   that waits on nothing still wakes for its timers, and a program that
   waits on a socket does not spin.

   The self-pipe: the loop owns one pipe it writes a single byte into from a
   context outside the loop's drive (a signal handler today, a worker thread
   later). The poller registers the pipe's read end with the same set as the
   user fds, so an external wakeup interrupts the wait without a signal to
   the poll call itself. `ps_poller_wake()` writes the byte; the loop drains
   the pipe after the wait returns.

   Readiness is level-triggered, the way `selectors` and `poll` report it: a
   row is ready when the fd is ready right now. The caller must drain what it
   read; a row that stays ready stays ready. The self-pipe is the exception
   the poller owns: it drains the pipe itself so a wakeup does not spin the
   loop.

   The poller is not reentrant and is owned by the loop: one per thread that
   runs a loop, created lazily on the first poll and closed at reset. The
   kernel handle (a kqueue or an epoll set) persists across polls, so a
   registration made on one poll is still live on the next.
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

/* One watched fd: the caller's descriptor and the readiness the caller
   wants (POLLIN and POLLOUT). */
typedef struct {
    int fd;
    int32_t events; /* POLLIN | POLLOUT */
    int32_t ready;  /* filled in by the poll */
} PsPollFd;

int ps_poller_poll(PsPollFd *fds, int count, int timeout_ms);
void ps_poller_wake(void);
int ps_poller_pipe_fd(void);
void ps_poller_reset(void);

/* The rows one poll watches. The loop's fd table is small; the cap keeps
   the backend's event array on the stack. */
#define PS_POLL_MAX 65

/* The self-pipe. Both ends are non-blocking: the write end so a wakeup that
   races a full pipe cannot block the writer, the read end so draining the
   pipe never blocks when the pipe runs dry. */
static int ps_pipe_read = -1;
static int ps_pipe_write = -1;

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

/* Drain the self-pipe after a poll that reported it ready. The read end is
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

#if defined(PS_POLLER_KQUEUE)

/* The kqueue lives across polls; a registration made on one poll is still
   live on the next. */
static int ps_kq = -1;

/* What the kqueue knows about one fd. */
typedef struct {
    int fd;
    int has_read;
    int has_write;
} PsReg;

static PsReg ps_regs[PS_POLL_MAX];
static int ps_nregs = 0;

static PsReg *ps_reg_find(int fd) {
    for (int i = 0; i < ps_nregs; i++) {
        if (ps_regs[i].fd == fd) {
            return &ps_regs[i];
        }
    }
    return NULL;
}

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
    ps_nregs = 0;
}

static void ps_kq_apply(int fd, int want_read, int want_write, int *read_on, int *write_on) {
    struct kevent changes[2];
    int n = 0;
    if (want_read && !*read_on) {
        EV_SET(&changes[n++], fd, EVFILT_READ, EV_ADD, 0, 0, NULL);
    }
    if (!want_read && *read_on) {
        EV_SET(&changes[n++], fd, EVFILT_READ, EV_DELETE, 0, 0, NULL);
    }
    if (want_write && !*write_on) {
        EV_SET(&changes[n++], fd, EVFILT_WRITE, EV_ADD, 0, 0, NULL);
    }
    if (!want_write && *write_on) {
        EV_SET(&changes[n++], fd, EVFILT_WRITE, EV_DELETE, 0, 0, NULL);
    }
    if (n > 0) {
        (void)kevent(ps_kq, changes, n, NULL, 0, NULL);
    }
    *read_on = want_read;
    *write_on = want_write;
}

int ps_poller_poll(PsPollFd *fds, int count, int timeout_ms) {
    ps_pipe_init();
    int kq = ps_kq_open();
    if (kq < 0) {
        return -1;
    }
    /* The pipe is always watched: it is the loop's own wakeup path. */
    PsReg *pipe_reg = ps_reg_find(ps_pipe_read);
    if (pipe_reg == NULL && ps_nregs < PS_POLL_MAX) {
        pipe_reg = &ps_regs[ps_nregs++];
        pipe_reg->fd = ps_pipe_read;
        pipe_reg->has_read = 0;
        pipe_reg->has_write = 0;
    }
    if (pipe_reg != NULL) {
        ps_kq_apply(ps_pipe_read, 1, 0, &pipe_reg->has_read, &pipe_reg->has_write);
    }
    for (int i = 0; i < count; i++) {
        PsReg *reg = ps_reg_find(fds[i].fd);
        if (reg == NULL) {
            if (ps_nregs >= PS_POLL_MAX) {
                continue;
            }
            reg = &ps_regs[ps_nregs++];
            reg->fd = fds[i].fd;
            reg->has_read = 0;
            reg->has_write = 0;
        }
        ps_kq_apply(fds[i].fd, (fds[i].events & POLLIN) != 0, (fds[i].events & POLLOUT) != 0,
                    &reg->has_read, &reg->has_write);
        fds[i].ready = 0;
    }
    /* Drop fds the caller no longer watches. */
    for (int i = 0; i < ps_nregs;) {
        int fd = ps_regs[i].fd;
        if (fd == ps_pipe_read) {
            i++;
            continue;
        }
        int wanted = 0;
        for (int j = 0; j < count; j++) {
            if (fds[j].fd == fd) {
                wanted = 1;
                break;
            }
        }
        if (wanted) {
            i++;
            continue;
        }
        struct kevent changes[2];
        int n = 0;
        if (ps_regs[i].has_read) {
            EV_SET(&changes[n++], fd, EVFILT_READ, EV_DELETE, 0, 0, NULL);
        }
        if (ps_regs[i].has_write) {
            EV_SET(&changes[n++], fd, EVFILT_WRITE, EV_DELETE, 0, 0, NULL);
        }
        if (n > 0) {
            (void)kevent(ps_kq, changes, n, NULL, 0, NULL);
        }
        ps_regs[i] = ps_regs[ps_nregs - 1];
        ps_nregs--;
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

int ps_poller_poll(PsPollFd *fds, int count, int timeout_ms) {
    ps_pipe_init();
    int ep = ps_ep_open();
    if (ep < 0) {
        return -1;
    }
    struct epoll_event events[PS_POLL_MAX];
    if (ps_pipe_read >= 0) {
        struct epoll_event ev;
        memset(&ev, 0, sizeof ev);
        ev.events = EPOLLIN;
        ev.data.fd = ps_pipe_read;
        (void)epoll_ctl(ep, EPOLL_CTL_ADD, ps_pipe_read, &ev);
    }
    for (int i = 0; i < count; i++) {
        struct epoll_event ev;
        memset(&ev, 0, sizeof ev);
        ev.events = 0;
        if (fds[i].events & POLLIN) {
            ev.events |= EPOLLIN;
        }
        if (fds[i].events & POLLOUT) {
            ev.events |= EPOLLOUT;
        }
        ev.data.fd = fds[i].fd;
        if (epoll_ctl(ep, EPOLL_CTL_ADD, fds[i].fd, &ev) != 0) {
            (void)epoll_ctl(ep, EPOLL_CTL_MOD, fds[i].fd, &ev);
        }
        fds[i].ready = 0;
    }
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
    if (ps_pipe_read >= 0) {
        close(ps_pipe_read);
        close(ps_pipe_write);
        ps_pipe_read = ps_pipe_write = -1;
    }
}

#else /* poll() fallback */

int ps_poller_poll(PsPollFd *fds, int count, int timeout_ms) {
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

typedef struct {
    int fd;
    int32_t events;
    int32_t ready;
} PsPollFd;

int ps_poller_poll(PsPollFd *fds, int count, int timeout_ms) {
    (void)fds;
    (void)count;
    (void)timeout_ms;
    return 0;
}

void ps_poller_wake(void) {}

int ps_poller_pipe_fd(void) { return -1; }

void ps_poller_reset(void) {}

#endif /* __wasi__ || _WIN32 */
