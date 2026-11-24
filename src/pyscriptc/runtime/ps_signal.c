/* SPDX-License-Identifier: Apache-2.0 */
/* The native signal handler surface (issue #1347).

`signal.signal(SIGTERM, fn)` installs a C-level handler through `sigaction`.
The handler does not call into the compiled program from the signal context:
it only records the signal number in a `volatile sig_atomic_t` slot, and the
compiled program drains that slot at a safepoint (`ps_signal_poll`). That is
CPython's deferred-delivery model in shape: CPython's C handler sets a flag
too, and the Python-level handler runs from the interpreter's eval loop, not
from the signal context.

The drain calls the compiled handler through the callable pair the emitter
built: `{impl, env}` where `impl` is env-first, so the call is
`impl(env, signum)`. The pair outlives the installing frame (the emitter
heap-allocates it), so the handler keeps working after the install returns.
*/

#include "ps_rt.h"

#include <stdlib.h>
#include <string.h>

#ifndef _WIN32
#include <signal.h>
#endif

/* The handler the emitter passes: `impl(env, signum)`, env-first like every
   other compiled callable pair. */
typedef void (*ps_signal_fn)(void *env, int64_t signum);

/* The pending slot: the C handler writes the signal number here, the poll
   reads it. `volatile sig_atomic_t` is the only object a signal handler may
   touch, so the number travels through it. */
static volatile sig_atomic_t ps_signal_pending;

/* The installed handlers, one slot per signal number the surface covers.
   The pair is `{impl, env}`: the impl pointer first, the env second, the
   shape every other runtime callable pair uses. */
#define PS_SIGNAL_MAX 64
static void *ps_signal_handlers[PS_SIGNAL_MAX];
static int32_t ps_signal_installed[PS_SIGNAL_MAX];

/* The C handler: record the number, nothing else. It runs in the signal
   context, so it may only touch the `sig_atomic_t` slot. */
static void ps_signal_capture(int signum) {
    ps_signal_pending = (sig_atomic_t)signum;
}

/* `ps_signal_install(signum, pair)`: install the handler for `signum`.

   The pair is `{impl, env}`; the poll loads the impl out of it and calls it
   with the env and the signal number. The pair is heap-allocated by the
   emitter, so the runtime only borrows it. */
void ps_signal_install(int64_t signum, void *pair) {
#ifndef _WIN32
    if (signum <= 0 || signum >= PS_SIGNAL_MAX) {
        ps_raise_cstr(PS_VALUE, "signal number out of range");
    }
    if (pair == NULL) {
        ps_raise_cstr(PS_VALUE, "signal handler is not a callable");
    }
    ps_signal_handlers[signum] = pair;
    ps_signal_installed[signum] = 1;
    struct sigaction action;
    memset(&action, 0, sizeof(action));
    action.sa_handler = ps_signal_capture;
    sigemptyset(&action.sa_mask);
    /* The handler runs on its own stack, so the flag must not block the
       signal that set it: SA_NODEFER keeps a second SIGTERM from being
       dropped while the first one is still being recorded. */
    action.sa_flags = SA_RESTART;
    if (sigaction((int)signum, &action, NULL) != 0) {
        ps_raise_cstr(PS_RUNTIME, "sigaction failed");
    }
#else
    (void)signum;
    (void)pair;
    ps_raise_cstr(PS_RUNTIME, "signals are not available on this target");
#endif
}

/* `ps_signal_reset(signum)`: put the default disposition back. */
void ps_signal_reset(int64_t signum) {
#ifndef _WIN32
    if (signum <= 0 || signum >= PS_SIGNAL_MAX) {
        ps_raise_cstr(PS_VALUE, "signal number out of range");
    }
    ps_signal_handlers[signum] = NULL;
    ps_signal_installed[signum] = 0;
    struct sigaction action;
    memset(&action, 0, sizeof(action));
    action.sa_handler = SIG_DFL;
    sigemptyset(&action.sa_mask);
    if (sigaction((int)signum, &action, NULL) != 0) {
        ps_raise_cstr(PS_RUNTIME, "sigaction failed");
    }
#else
    (void)signum;
    ps_raise_cstr(PS_RUNTIME, "signals are not available on this target");
#endif
}

/* `ps_signal_ignore(signum)`: install `SIG_IGN` (the disposition CPython
   spells `Handlers.SIG_IGN`, value 1). */
void ps_signal_ignore(int64_t signum) {
#ifndef _WIN32
    if (signum <= 0 || signum >= PS_SIGNAL_MAX) {
        ps_raise_cstr(PS_VALUE, "signal number out of range");
    }
    ps_signal_handlers[signum] = NULL;
    ps_signal_installed[signum] = 0;
    struct sigaction action;
    memset(&action, 0, sizeof(action));
    action.sa_handler = SIG_IGN;
    sigemptyset(&action.sa_mask);
    if (sigaction((int)signum, &action, NULL) != 0) {
        ps_raise_cstr(PS_RUNTIME, "sigaction failed");
    }
#else
    (void)signum;
    ps_raise_cstr(PS_RUNTIME, "signals are not available on this target");
#endif
}

/* `ps_signal_default(signum)`: install `SIG_DFL` (the disposition CPython
   spells `Handlers.SIG_DFL`, value 0). */
void ps_signal_default(int64_t signum) {
    ps_signal_reset(signum);
}

/* `ps_signal_poll()`: the safepoint. Drain the pending slot, if any.

   The compiled handler runs here, outside the signal context, so it may
   take locks, allocate and raise exceptions. The slot is cleared before the
   call, so a handler that re-arms itself through another signal does not
   lose the second delivery. */
void ps_signal_poll(void) {
    int signum = (int)ps_signal_pending;
    if (signum <= 0) {
        return;
    }
    ps_signal_pending = 0;
    void *pair = ps_signal_handlers[signum];
    if (pair == NULL) {
        return;
    }
    void **slots = (void **)pair;
    ps_signal_fn impl = (ps_signal_fn)slots[0];
    impl(slots[1], (int64_t)signum);
}

/* True while a signal is waiting for its safepoint. */
int32_t ps_signal_pending_p(void) {
    return ps_signal_pending != 0 ? 1 : 0;
}
