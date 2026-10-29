/* The selectors substrate (#485): DefaultSelector over the one ps_poller.c
   event loop, at the runtime level.

   The scope decision the issue asks for: the subset is a *runtime-level*
   API, not a Python module, until the typed `socket` surface exists
   (#229). The reason: CPython's selectors take `fileobj` (anything with
   `fileno()`), and the closed subset has no `socket.socket` object to take.
   A Python module whose register() accepts only bare ints would look like
   selectors and behave differently on the first line a tester writes.
   When #229 lands the typed socket handle, a thin `selectors` module can
   lower onto these records and read the fd off the handle; the records
   here do not change.

   The surface, one selector record per call to ps_select_new:

     ps_select_new()                    -> Sel*
     ps_select_register(sel, fd, events)-> SelKey*   (raises on a double register)
     ps_select_unregister(sel, fd)      -> SelKey*   (raises on an unknown fd)
     ps_select_modify(sel, fd, events)  -> SelKey*   (register semantics on a live fd)
     ps_select_select(sel, timeout_s)   -> PsSeq*    of SelKey*, ready ones first
     ps_select_get_key(sel, fd)         -> SelKey*   (None when absent; the caller checks)
     ps_select_key_fd(key)              -> int
     ps_select_key_events(key)          -> int
     ps_select_close(sel)               -> void

   `events` is the CPython bit mask: bit 0 EVENT_READ, bit 1 EVENT_WRITE.
   `select(timeout)` mirrors selectors.DefaultSelector.select: timeout None
   blocks, 0 polls, negative means "block" (CPython treats negatives as
   None), None-ish here is a negative double.

   A key is a heap record { fd, events, data? } — data stays a Python-level
   idea, so the runtime key carries only the two ints and the Python layer
   (when it exists) holds the rest. select() returns the keys of the ready
   fds, level-triggered: a fd that stays ready stays ready, the way
   selectors reports it. The poller owns the self-pipe; this file never
   wakes anything.

   One selector per loop is the normal shape, but nothing enforces it: the
   records are independent and the poller's registration table tracks the
   union. Closing a selector unregisters its fds from the shared poller on
   the next poll (the rows go away when no selector watches them).
*/

#include "ps_rt.h"

#if !defined(__wasi__) && !defined(_WIN32)

#include <stdlib.h>
#include <string.h>

#include "ps_poller.h"

/* CPython's selectors module constants. */
#define PS_EVENT_READ 1
#define PS_EVENT_WRITE 2

/* POLLIN / POLLOUT on every backend this file compiles for. */
#define PS_POLLIN 1
#define PS_POLLOUT 4
typedef struct PsSelKey {
    int fd;
    int events; /* PS_EVENT_* bits */
    struct PsSelKey *next;
} PsSelKey;

typedef struct PsSel {
    PsSelKey *keys;
} PsSel;

static int ps_events_to_poll(int events) {
    int out = 0;
    if (events & PS_EVENT_READ) {
        out |= PS_POLLIN;
    }
    if (events & PS_EVENT_WRITE) {
        out |= PS_POLLOUT;
    }
    return out;
}

static PsSelKey *ps_sel_find(PsSel *sel, int fd) {
    for (PsSelKey *key = sel->keys; key != NULL; key = key->next) {
        if (key->fd == fd) {
            return key;
        }
    }
    return NULL;
}

void *ps_select_new(void) {
    PsSel *sel = calloc(1, sizeof(PsSel));
    if (sel == NULL) {
        ps_raise_cstr(PS_RUNTIME, "out of memory");
    }
    return sel;
}

void *ps_select_register(void *box, int64_t fd, int64_t events) {
    PsSel *sel = (PsSel *)box;
    if (sel == NULL) {
        ps_raise_cstr(PS_RUNTIME, "selector is not initialized");
    }
    if (fd < 0) {
        ps_raise_cstr(PS_RUNTIME, "register fd is a non-negative int");
    }
    if (ps_sel_find(sel, (int)fd) != NULL) {
        /* CPython: KeyError on a double register. */
        ps_raise_cstr(PS_RUNTIME, "fd is already registered");
    }
    PsSelKey *key = calloc(1, sizeof(PsSelKey));
    if (key == NULL) {
        ps_raise_cstr(PS_RUNTIME, "out of memory");
    }
    key->fd = (int)fd;
    key->events = (int)events;
    key->next = sel->keys;
    sel->keys = key;
    ps_poller_watch((int)fd, ps_events_to_poll(key->events));
    return key;
}

void *ps_select_unregister(void *box, int64_t fd) {
    PsSel *sel = (PsSel *)box;
    if (sel == NULL) {
        ps_raise_cstr(PS_RUNTIME, "selector is not initialized");
    }
    PsSelKey **link = &sel->keys;
    while (*link != NULL) {
        if ((*link)->fd == (int)fd) {
            PsSelKey *key = *link;
            *link = key->next;
            ps_poller_unwatch((int)fd, ps_events_to_poll(key->events));
            return key;
        }
        link = &(*link)->next;
    }
    ps_raise_cstr(PS_RUNTIME, "fd is not registered");
    return NULL;
}

void *ps_select_modify(void *box, int64_t fd, int64_t events) {
    PsSel *sel = (PsSel *)box;
    if (sel == NULL) {
        ps_raise_cstr(PS_RUNTIME, "selector is not initialized");
    }
    PsSelKey *key = ps_sel_find(sel, (int)fd);
    if (key == NULL) {
        ps_raise_cstr(PS_RUNTIME, "fd is not registered");
    }
    ps_poller_unwatch((int)fd, ps_events_to_poll(key->events));
    key->events = (int)events;
    ps_poller_watch((int)fd, ps_events_to_poll(key->events));
    return key;
}

void *ps_select_get_key(void *box, int64_t fd) {
    PsSel *sel = (PsSel *)box;
    if (sel == NULL) {
        ps_raise_cstr(PS_RUNTIME, "selector is not initialized");
    }
    return ps_sel_find(sel, (int)fd);
}

int64_t ps_select_key_fd(void *key) {
    return key == NULL ? -1 : ((PsSelKey *)key)->fd;
}

int64_t ps_select_key_events(void *key) {
    return key == NULL ? 0 : ((PsSelKey *)key)->events;
}

PsSeq *ps_select_select(void *box, double timeout) {
    PsSel *sel = (PsSel *)box;
    if (sel == NULL) {
        ps_raise_cstr(PS_RUNTIME, "selector is not initialized");
    }
    int rows = 0;
    for (PsSelKey *key = sel->keys; key != NULL; key = key->next) {
        rows++;
    }
    PsPollFd *fds = rows == 0 ? NULL : malloc((size_t)rows * sizeof(PsPollFd));
    if (rows > 0 && fds == NULL) {
        ps_raise_cstr(PS_RUNTIME, "out of memory");
    }
    int n = 0;
    for (PsSelKey *key = sel->keys; key != NULL; key = key->next) {
        fds[n].fd = key->fd;
        fds[n].events = ps_events_to_poll(key->events);
        fds[n].ready = 0;
        n++;
    }
    /* CPython's timeout: None blocks; a negative counts as None; 0 polls. */
    int timeout_ms = -1;
    if (timeout >= 0.0) {
        timeout_ms = (int)(timeout * 1000.0);
    }
    int got = ps_poller_wait(fds, n, timeout_ms);
    if (got < 0) {
        free(fds);
        ps_raise_cstr(PS_RUNTIME, "select failed");
    }
    /* The ready keys, in registration order. CPython returns only the ready
       rows; an empty poll (timeout hit, nothing ready) is an empty list. */
    PsSeq *out = ps_seq_new((int64_t)sizeof(void *), 0);
    if (got > 0) {
        for (PsSelKey *key = sel->keys; key != NULL; key = key->next) {
            for (int i = 0; i < n; i++) {
                if (fds[i].fd == key->fd && fds[i].ready) {
                    ps_seq_push(out, &key);
                    break;
                }
            }
        }
    }
    free(fds);
    return out;
}

void ps_select_close(void *box) {
    PsSel *sel = (PsSel *)box;
    if (sel == NULL) {
        return;
    }
    PsSelKey *key = sel->keys;
    while (key != NULL) {
        PsSelKey *next = key->next;
        free(key);
        key = next;
    }
    free(sel);
}

#else /* __wasi__ || _WIN32 */

/* No selector on a target without sockets: the records exist so the link
   stays whole, and every call raises the runtime error a WASI program
   would hit at the first select anyway. */

void *ps_select_new(void) {
    ps_raise_cstr(PS_RUNTIME, "selectors needs sockets, which this target has no story for");
    return NULL;
}

void *ps_select_register(void *box, int64_t fd, int64_t events) {
    (void)box;
    (void)fd;
    (void)events;
    ps_raise_cstr(PS_RUNTIME, "selectors needs sockets, which this target has no story for");
    return NULL;
}

void *ps_select_unregister(void *box, int64_t fd) {
    (void)box;
    (void)fd;
    ps_raise_cstr(PS_RUNTIME, "selectors needs sockets, which this target has no story for");
    return NULL;
}

void *ps_select_modify(void *box, int64_t fd, int64_t events) {
    (void)box;
    (void)fd;
    (void)events;
    ps_raise_cstr(PS_RUNTIME, "selectors needs sockets, which this target has no story for");
    return NULL;
}

void *ps_select_get_key(void *box, int64_t fd) {
    (void)box;
    (void)fd;
    ps_raise_cstr(PS_RUNTIME, "selectors needs sockets, which this target has no story for");
    return NULL;
}

int64_t ps_select_key_fd(void *key) {
    (void)key;
    return -1;
}

int64_t ps_select_key_events(void *key) {
    (void)key;
    return 0;
}

PsSeq *ps_select_select(void *box, double timeout) {
    (void)box;
    (void)timeout;
    ps_raise_cstr(PS_RUNTIME, "selectors needs sockets, which this target has no story for");
    return NULL;
}

void ps_select_close(void *box) {
    (void)box;
}

#endif /* __wasi__ || _WIN32 */
