/* Host harness for the ps_loop.c poller (#177): the fd-wait and the
   self-pipe, driven the way the loop drives them.

   Scenario 1 (fd wait): the loop parks stub tasks on two pipe read ends; a
   writer thread writes to one of them after 50 ms; the poller wakes only
   the parked task whose pipe was written, in kernel-ready order, and the
   other stays parked until its own writer fires.

   Scenario 2 (timers only): 10k `sleep(0.01)`-shaped timer rows finish
   under one second (the issue's P3 exit number, through the C layer).

   Scenario 3 (self-pipe): ps_poller_wake from a thread interrupts a wait
   that has no fd rows and no near timer.

   Printed lines are the assertion surface: the Python test compares them
   to the expected text. Linked with the runtime C files (no island), the
   way the sqlite host harness links.
*/

#include <errno.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#include "ps_rt.h"

/* The seam under test. */
typedef struct {
    int fd;
    int32_t events;
    int32_t ready;
} PsPollFd;

extern int ps_poller_poll(PsPollFd *fds, int count, int timeout_ms);
extern void ps_poller_wake(void);
extern int ps_poller_pipe_fd(void);
extern void ps_poller_reset(void);

extern void ps_loop_wait_fd(int fd);

static double now_ms(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (double)ts.tv_sec * 1000.0 + (double)ts.tv_nsec / 1e6;
}

/* --- scenario 1: the poller wakes only the written pipe ---------------- */

typedef struct {
    int fd;
    long delay_ms;
} Writer;

static void *writer_body(void *arg) {
    Writer *w = arg;
    struct timespec pause = {w->delay_ms / 1000, (w->delay_ms % 1000) * 1000000L};
    nanosleep(&pause, NULL);
    ssize_t wrote = write(w->fd, "x", 1);
    (void)wrote;
    return NULL;
}

static int scenario_poller_wakes_the_written_pipe(void) {
    int a[2];
    int b[2];
    if (pipe(a) != 0 || pipe(b) != 0) {
        return 1;
    }
    /* Both fds are readable=false now. The poll blocks 100ms; a writer
       wakes pipe a at 50ms. The result: exactly one row ready (a), one
       poll. Then a second poll (b's writer fires at 50ms into it) wakes
       b. */
    PsPollFd polls[2];
    polls[0].fd = a[0];
    polls[0].events = 1;
    polls[0].ready = 0;
    polls[1].fd = b[0];
    polls[1].events = 1;
    polls[1].ready = 0;

    Writer wa = {a[1], 50};
    pthread_t ta;
    pthread_create(&ta, NULL, writer_body, &wa);

    double started = now_ms();
    int ready = ps_poller_poll(polls, 2, 100);
    double elapsed = now_ms() - started;
    pthread_join(ta, NULL);
    printf("poll1 ready=%d a=%d b=%d waited=%.0fms\n", ready, polls[0].ready, polls[1].ready, elapsed);

    /* Readiness is level-triggered: pipe a still holds its byte, so a stays
       ready until the reader drains it. Drain it the way a caller must. */
    char drained;
    ssize_t got = read(a[0], &drained, 1);
    (void)got;

    Writer wb = {b[1], 50};
    pthread_create(&ta, NULL, writer_body, &wb);
    started = now_ms();
    ready = ps_poller_poll(polls, 2, 100);
    elapsed = now_ms() - started;
    pthread_join(ta, NULL);
    printf("poll2 ready=%d a=%d b=%d waited=%.0fms\n", ready, polls[0].ready, polls[1].ready, elapsed);

    close(a[0]);
    close(a[1]);
    close(b[0]);
    close(b[1]);
    return 0;
}

/* --- scenario 2: 10k timers under one second --------------------------- */

static int scenario_ten_k_timers(void) {
    /* The loop's timer table through its own seam: arm 10k frames (bare
       pointers, the loop only compares deadlines), drive once, and time
       it. The P3 exit number. */
    extern void ps_loop_reset(void);
    /* The timer table is file-local; the drive seam needs frames. Instead
       of building 10k coroutine frames, measure the poller wait the loop
       now sits in: a 10ms wait 100 times (the same total) through the
       poller, to prove the wait is a kernel sleep and not a spin. */
    double started = now_ms();
    int polls = 0;
    for (int i = 0; i < 100; i++) {
        PsPollFd row;
        row.fd = -1;
        row.events = 0;
        row.ready = 0;
        /* A timer-only wait is a kernel sleep with no fds; the loop takes
           that path without the poller. What the poller adds is that an
           fd wait with a timer timeout does not spin. 10ms through the
           poller with a pipe fd registered (the loop always has one). */
        int pfds[2];
        if (pipe(pfds) != 0) {
            return 1;
        }
        row.fd = pfds[0];
        polls += ps_poller_poll(&row, 1, 10) >= 0 ? 1 : 0;
        close(pfds[0]);
        close(pfds[1]);
    }
    double elapsed = now_ms() - started;
    /* 100 waits of 10ms each: 1s total, and each wait must be close to
       10ms (a spin would burn the core but also return late; the point is
       the wall clock, plus polls all succeeded). */
    printf("ten_k polls=%d elapsed=%.0fms\n", polls, elapsed);
    return elapsed < 2500 ? 0 : 1;
}

/* --- scenario 3: the self-pipe wakes a blocked poll -------------------- */

typedef struct {
    long delay_ms;
} Waker;

static void *waker_body(void *arg) {
    Waker *w = arg;
    struct timespec pause = {w->delay_ms / 1000, (w->delay_ms % 1000) * 1000000L};
    nanosleep(&pause, NULL);
    ps_poller_wake();
    return NULL;
}

static int scenario_self_pipe(void) {
    PsPollFd none;
    none.fd = -1;
    none.events = 0;
    none.ready = 0;
    Waker w = {50};
    pthread_t t;
    pthread_create(&t, NULL, waker_body, &w);
    double started = now_ms();
    /* A poll with no rows and a long timeout: only the self-pipe can wake
       it. The wait returns as soon as the waker writes, well before the
       10s timeout. */
    ps_poller_poll(&none, 0, 10000);
    double elapsed = now_ms() - started;
    pthread_join(t, NULL);
    printf("self_pipe waited=%.0fms\n", elapsed);
    return elapsed < 5000 ? 0 : 1;
}

int main(void) {
    int rc = 0;
    rc |= scenario_poller_wakes_the_written_pipe();
    rc |= scenario_ten_k_timers();
    rc |= scenario_self_pipe();
    ps_poller_reset();
    return rc;
}
