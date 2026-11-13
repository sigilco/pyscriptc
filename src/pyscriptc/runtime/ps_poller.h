/* The fd poller's interface (#177, #485).

   The poller is the kernel interface behind the event loop: kqueue on macOS
   and the BSDs, epoll on Linux, a poll() fallback elsewhere. ps_poller.c
   holds the backends; this header is the shape the loop (ps_loop.c) and the
   selector (ps_select.c) share.

   The interest in an fd is counted, not replaced: the loop watches the fds
   its tasks park on and the selector watches the fds a program registered,
   and both can name the same fd. The kernel registration lives while a
   count is above zero.

   Readiness is level-triggered, the way `selectors` and `poll` report it: a
   row is ready when the fd is ready right now, and it stays ready until the
   caller drains it.
*/

#ifndef PS_POLLER_H
#define PS_POLLER_H

/* One watched fd: the descriptor, the readiness the caller wants (POLLIN
   and POLLOUT), and the readiness the wait found. */
typedef struct {
    int fd;
    int events;
    int ready;
} PsPollFd;

/* Register (or add to) interest in an fd. events is POLLIN | POLLOUT. */
void ps_poller_watch(int fd, int events);

/* Drop one watcher's interest in an fd. The kernel registration goes away
   when the last watcher lets go. */
void ps_poller_unwatch(int fd, int events);

/* Wait for any of the fds in `fds` to become ready, up to timeout_ms
   (a negative timeout waits forever). Returns the number of rows with
   `ready` set, or -1 on a kernel error. */
int ps_poller_wait(PsPollFd *fds, int count, int timeout_ms);

/* The self-pipe: write a byte to wake a wait, and the fd the loop drains. */
void ps_poller_wake(void);
int ps_poller_pipe_fd(void);

/* Drop every registration and the self-pipe (the loop's reset). */
void ps_poller_reset(void);

#endif /* PS_POLLER_H */
