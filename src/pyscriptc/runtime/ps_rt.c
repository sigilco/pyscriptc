/* Small runtime linked beside emitted LLVM.

Lists and dicts are pointers, so assignment shares the buffer the way Python
does. A try runs its body as a callback: setjmp stays in ps_try_call, whose
frame is still live if the body raises. Arithmetic overflow stays an LLVM trap.
*/

#include "ps_rt.h"
#include "ps_case.c"

#include <math.h>
#include <setjmp.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct PsJmp {
    ps_sigjmp_buf buf;
    struct PsJmp *prev;
} PsJmp;

static _Thread_local PsJmp *ps_top;
static _Thread_local PsExc *ps_current;
char ps_empty_byte;

/* Free list for the jump slots. A `try` runs once per loop iteration in hot
code (a generator's `for` body is a try), so the slot allocation must not be
a malloc/free pair per iteration: the free list reuses the last slot in
amortized constant time. Slots are thread-local and never freed back to the
heap, so a deep nesting peak sets the high-water mark. */
static _Thread_local PsJmp *ps_jmp_free;

/* Parent chain for the builtin codes (E2 M2): a parent always has a smaller
code than its children, so the walk towards zero ends at the root. User
codes (>= PS_USER_BASE) register their parent and name at module init
through ps_exc_register. */
static const int32_t ps_exc_parent[PS_USER_BASE] = {
    0,                       /* unused index 0: the root's parent */
    0,                       /* BaseException: the root */
    PS_BASEEXCEPTION,        /* BaseExceptionGroup */
    PS_BASEEXCEPTIONGROUP,   /* ExceptionGroup */
    PS_BASEEXCEPTION,        /* GeneratorExit */
    PS_BASEEXCEPTION,        /* KeyboardInterrupt */
    PS_BASEEXCEPTION,        /* SystemExit */
    PS_BASEEXCEPTION,        /* Exception */
    PS_EXCEPTION,            /* ArithmeticError */
    PS_ARITHMETICERROR,      /* FloatingPointError */
    PS_ARITHMETICERROR,      /* OverflowError */
    PS_ARITHMETICERROR,      /* ZeroDivisionError */
    PS_EXCEPTION,            /* AssertionError */
    PS_EXCEPTION,            /* AttributeError */
    PS_EXCEPTION,            /* BufferError */
    PS_EXCEPTION,            /* EOFError */
    PS_EXCEPTION,            /* ImportError */
    PS_IMPORTERROR,          /* ModuleNotFoundError */
    PS_EXCEPTION,            /* LookupError */
    PS_LOOKUPERROR,          /* IndexError */
    PS_LOOKUPERROR,          /* KeyError */
    PS_EXCEPTION,            /* MemoryError */
    PS_EXCEPTION,            /* NameError */
    PS_NAMEERROR,            /* UnboundLocalError */
    PS_EXCEPTION,            /* OSError */
    PS_OSERROR,              /* BlockingIOError */
    PS_OSERROR,              /* ChildProcessError */
    PS_OSERROR,              /* ConnectionError */
    PS_CONNECTIONERROR,      /* BrokenPipeError */
    PS_CONNECTIONERROR,      /* ConnectionAbortedError */
    PS_CONNECTIONERROR,      /* ConnectionRefusedError */
    PS_CONNECTIONERROR,      /* ConnectionResetError */
    PS_OSERROR,              /* FileExistsError */
    PS_OSERROR,              /* FileNotFoundError */
    PS_OSERROR,              /* InterruptedError */
    PS_OSERROR,              /* IsADirectoryError */
    PS_OSERROR,              /* NotADirectoryError */
    PS_OSERROR,              /* PermissionError */
    PS_OSERROR,              /* ProcessLookupError */
    PS_OSERROR,              /* TimeoutError */
    PS_EXCEPTION,            /* ReferenceError */
    PS_EXCEPTION,            /* RuntimeError */
    PS_RUNTIMEERROR,         /* NotImplementedError */
    PS_RUNTIMEERROR,         /* RecursionError */
    PS_EXCEPTION,            /* StopAsyncIteration */
    PS_EXCEPTION,            /* StopIteration */
    PS_EXCEPTION,            /* SyntaxError */
    PS_SYNTAXERROR,          /* IndentationError */
    PS_INDENTATIONERROR,     /* TabError */
    PS_EXCEPTION,            /* SystemError */
    PS_EXCEPTION,            /* TypeError */
    PS_EXCEPTION,            /* ValueError */
    PS_VALUEERROR,           /* UnicodeError */
    PS_UNICODEERROR,         /* UnicodeDecodeError */
    PS_UNICODEERROR,         /* UnicodeEncodeError */
    PS_UNICODEERROR,         /* UnicodeTranslateError */
    PS_EXCEPTION,            /* Warning */
    PS_WARNING,              /* BytesWarning */
    PS_WARNING,              /* DeprecationWarning */
    PS_WARNING,              /* EncodingWarning */
    PS_WARNING,              /* FutureWarning */
    PS_WARNING,              /* ImportWarning */
    PS_WARNING,              /* PendingDeprecationWarning */
    PS_WARNING,              /* ResourceWarning */
    PS_WARNING,              /* RuntimeWarning */
    PS_WARNING,              /* SyntaxWarning */
    PS_WARNING,              /* UnicodeWarning */
    PS_WARNING,              /* UserWarning */
};

/* Class names for the builtin codes, indexed by code. Index 0 is unused. */
static const char *const ps_exc_names[PS_USER_BASE] = {
    NULL,
    "BaseException",
    "BaseExceptionGroup",
    "ExceptionGroup",
    "GeneratorExit",
    "KeyboardInterrupt",
    "SystemExit",
    "Exception",
    "ArithmeticError",
    "FloatingPointError",
    "OverflowError",
    "ZeroDivisionError",
    "AssertionError",
    "AttributeError",
    "BufferError",
    "EOFError",
    "ImportError",
    "ModuleNotFoundError",
    "LookupError",
    "IndexError",
    "KeyError",
    "MemoryError",
    "NameError",
    "UnboundLocalError",
    "OSError",
    "BlockingIOError",
    "ChildProcessError",
    "ConnectionError",
    "BrokenPipeError",
    "ConnectionAbortedError",
    "ConnectionRefusedError",
    "ConnectionResetError",
    "FileExistsError",
    "FileNotFoundError",
    "InterruptedError",
    "IsADirectoryError",
    "NotADirectoryError",
    "PermissionError",
    "ProcessLookupError",
    "TimeoutError",
    "ReferenceError",
    "RuntimeError",
    "NotImplementedError",
    "RecursionError",
    "StopAsyncIteration",
    "StopIteration",
    "SyntaxError",
    "IndentationError",
    "TabError",
    "SystemError",
    "TypeError",
    "ValueError",
    "UnicodeError",
    "UnicodeDecodeError",
    "UnicodeEncodeError",
    "UnicodeTranslateError",
    "Warning",
    "BytesWarning",
    "DeprecationWarning",
    "EncodingWarning",
    "FutureWarning",
    "ImportWarning",
    "PendingDeprecationWarning",
    "ResourceWarning",
    "RuntimeWarning",
    "SyntaxWarning",
    "UnicodeWarning",
    "UserWarning",
};

/* User exception classes register here: the parent code and the class name.
The registry is a fixed table so the emitted module init can fill it without
allocating, and a program never registers more classes than this holds. */
#define PS_USER_EXC_MAX 1024
typedef struct PsUserExc {
    int32_t code;
    int32_t parent;
    const char *name;
} PsUserExc;
static PsUserExc ps_user_exc[PS_USER_EXC_MAX];
static int32_t ps_user_exc_n;

_Static_assert(PS_RESERVED_BASE > PS_USER_BASE, "reserved codes sit above user codes");
_Static_assert(PS_TIMEOUT >= PS_GENERATOR_EXIT && PS_TIMEOUT < 0x7FFFFFFF, "reserved codes fit i32");
_Static_assert(PS_QUEUE_FULL == PS_RESERVED_BASE + 5 && PS_QUEUE_FULL < 0x7FFFFFFF, "queue codes follow the coroutine codes");

_Static_assert(sizeof(PsBuf) == 16, "PsBuf must match %ps.str");

/* The class name for a code: the builtin table, then the user registry. */
static const char *ps_exc_name(int code) {
    if (code > 0 && code < PS_USER_BASE) {
        const char *name = ps_exc_names[code];
        if (name != NULL) {
            return name;
        }
    }
    switch (code) {
    case PS_GENERATOR_EXIT:
        return "GeneratorExit";
    case PS_STOP_ASYNC:
        return "StopAsyncIteration";
    case PS_CANCELLED:
        return "CancelledError";
    case PS_TIMEOUT:
        return "TimeoutError";
    case PS_QUEUE_EMPTY:
        return "Empty";
    case PS_QUEUE_FULL:
        return "Full";
    default:
        break;
    }
    if (code >= PS_USER_BASE) {
        int32_t index = code - PS_USER_BASE;
        if (index < ps_user_exc_n && ps_user_exc[index].name != NULL) {
            return ps_user_exc[index].name;
        }
    }
    return "RuntimeError";
}

static PsExc *ps_exc_new(int32_t code) {
    PsExc *e = calloc(1, sizeof(PsExc));
    if (e == NULL) {
        abort();
    }
    e->code = code;
    e->name.data = (char *)ps_exc_name(code);
    e->name.len = (int64_t)strlen(e->name.data);
    e->message.data = calloc(256, 1);
    if (e->message.data == NULL) {
        abort();
    }
    return e;
}

/* CPython prints a str message as its repr: quoted, with the escapes.
   The escape engine is ps_repr_str (ps_unicode.c), so the exception
   message, the tuple display and repr() agree byte for byte. */
void ps_report_quoted(PsGrow *g, const PsBuf *msg) {
    PsBuf quoted;
    ps_repr_str(msg->len, msg->data, &quoted);
    ps_grow_add(g, quoted.data, quoted.len);
    free(quoted.data);
}

/* The message a KeyError shows: CPython prints its argument as a repr. */
static PsBuf ps_exc_quoted(const PsBuf *msg) {
    PsGrow g;
    PsBuf out;
    ps_grow_init(&g);
    ps_report_quoted(&g, msg);
    ps_grow_finish(&g, &out);
    return out;
}

/* The `repr` of a str value: quoted, with the escapes. The tuple display
   calls this for its str fields (CPython's `tuple.__str__` is its repr). */
void ps_repr_quoted(int64_t n, const char *p, PsBuf *out) {
    PsGrow g;
    PsBuf msg;
    msg.len = n;
    msg.data = (char *)p;
    ps_grow_init(&g);
    ps_report_quoted(&g, &msg);
    ps_grow_finish(&g, out);
}

static void ps_report_one(PsExc *e) {
    if (e == NULL) {
        fprintf(stderr, "RuntimeError\n");
        return;
    }
    if (e->message.len > 0) {
        if (ps_exc_isa(e->code, PS_KEY) && (e->flags & PS_EXC_FLAG_PREQUOTED) == 0) {
            /* CPython prints a KeyError message as its repr: quoted. */
            PsBuf quoted = ps_exc_quoted(&e->message);
            fprintf(stderr, "%s: %s\n", ps_exc_name(e->code), quoted.data);
            free(quoted.data);
        } else {
            fprintf(stderr, "%s: %s\n", ps_exc_name(e->code), e->message.data);
        }
    } else {
        fprintf(stderr, "%s\n", ps_exc_name(e->code));
    }
}

/* Print the current exception the way the last-resort handler does. The
   prefix is an optional line printed first (the thread excepthook shape).
   The caller decides what happens next: the main path exits, a thread
   keeps the process running. */
void ps_report_current(const char *prefix) {
    PsExc *e = ps_current;
    if (prefix != NULL && prefix[0] != '\0') {
        fprintf(stderr, "%s\n", prefix);
    }
    if (e != NULL && e->cause != NULL) {
        ps_report_one(e->cause);
        fprintf(stderr, "The above exception was the direct cause of the following exception.\n");
    }
    ps_report_one(e);
}

/* The last-resort handler's SystemExit rule (#603), the way CPython's
   PyErr_PrintEx splits it: an int value is the process status, a str value is
   written to stderr and the process exits 1, and no value exits 0. No class
   name and no traceback text are printed. A SystemExit subclass follows the
   same rule: the parent chain decides, not the code. */
__attribute__((noinline, noreturn)) static void ps_exit_system_exit(void) {
    PsExc *e = ps_current;
    if (e != NULL && (e->flags & PS_EXC_FLAG_EXIT_STATUS) != 0) {
        exit((int)e->exit_status);
    }
    if (e != NULL && (e->flags & PS_EXC_FLAG_EXIT_MSG) != 0) {
        if (e->message.len > 0) {
            fwrite(e->message.data, 1, (size_t)e->message.len, stderr);
        }
        /* The newline is written whether or not the value ends in one. */
        fputc('\n', stderr);
        exit(1);
    }
    exit(0);
}

/* Deliver ps_current: unwind to the innermost handler, or report and exit. */
__attribute__((noinline, noreturn)) static void ps_deliver(void) {
    if (ps_top == NULL) {
        if (ps_current != NULL && ps_exc_isa(ps_current->code, PS_SYSTEMEXIT)) {
            ps_exit_system_exit();
        }
        ps_report_current(NULL);
        exit(1);
    }
    ps_siglongjmp(ps_top->buf, PS_RAISED);
}

PsExc *ps_exc_current(void) {
    return ps_current;
}

/* Restore the thread's "currently handled" exception.
   A generator frame keeps one exception of its own: a suspension inside an
   `except` body saves `ps_current` into the frame, and the resume puts it
   back, so a bare `raise` after the resume re-raises the exception the
   handler caught and not whatever the thread handled in between. */
void ps_exc_restore(PsExc *e) {
    ps_current = e;
}

PsExc *ps_exc_make(int32_t code, int64_t n, const char *data) {
    if (code == 0) {
        code = PS_RUNTIME;
    }
    if (n < 0) {
        n = 0;
    }
    size_t take = (size_t)n;
    if (take > 255) {
        take = 255;
    }
    PsExc *e = ps_exc_new(code);
    if (take > 0 && data != NULL) {
        memcpy(e->message.data, data, take);
    }
    e->message.data[take] = '\0';
    e->message.len = (int64_t)take;
    /* `raise SystemExit("bye") from cause` takes this path (a raise-from builds
       a record instead of calling ps_raise_msg): a value was given, so the
       uncaught handler prints it and exits 1. A bare `raise SystemExit from
       cause` passes NULL and still exits 0. */
    if (data != NULL && ps_exc_isa(e->code, PS_SYSTEMEXIT)) {
        e->flags |= PS_EXC_FLAG_EXIT_MSG;
    }
    return e;
}

void ps_exc_set_cause(PsExc *e, PsExc *cause) {
    if (e != NULL) {
        e->cause = cause;
    }
}

__attribute__((noinline, noreturn)) void ps_raise_exc(PsExc *e) {
    if (e == NULL) {
        e = ps_exc_new(PS_RUNTIME);
    }
    ps_current = e;
    ps_deliver();
}

void ps_exc_str(PsExc *e, PsBuf *out) {
    if (e == NULL) {
        out->len = 0;
        out->data = &ps_empty_byte;
        return;
    }
    /* CPython's KeyError shows its argument as a repr: str(KeyError('k'))
       is "'k'", while str(ValueError('k')) is "k". A message that already
       holds the repr (a missing dict key) is left alone. */
    if (ps_exc_isa(e->code, PS_KEY) && e->message.len > 0 && (e->flags & PS_EXC_FLAG_PREQUOTED) == 0) {
        PsBuf quoted = ps_exc_quoted(&e->message);
        *out = quoted;
        return;
    }
    *out = e->message;
}

void ps_exc_class(PsExc *e, PsBuf *out) {
    if (e == NULL) {
        out->len = 0;
        out->data = &ps_empty_byte;
        return;
    }
    *out = e->name;
}

/* CPython repr: Name('message') or Name() when raised without one. */
void ps_exc_repr(PsExc *e, PsBuf *out) {
    if (e == NULL) {
        out->len = 0;
        out->data = &ps_empty_byte;
        return;
    }
    int64_t quoted = e->message.len > 0 ? e->message.len + 2 : 0;
    int64_t n = e->name.len + 2 + quoted;
    char *q = malloc((size_t)n + 1);
    if (q == NULL) {
        abort();
    }
    int64_t at = 0;
    memcpy(q + at, e->name.data, (size_t)e->name.len);
    at += e->name.len;
    memcpy(q + at, "(", 1);
    at += 1;
    if (quoted > 0) {
        memcpy(q + at, "'", 1);
        at += 1;
        memcpy(q + at, e->message.data, (size_t)e->message.len);
        at += e->message.len;
        memcpy(q + at, "'", 1);
        at += 1;
    }
    memcpy(q + at, ")", 1);
    at += 1;
    q[at] = '\0';
    out->len = at;
    out->data = q;
}

/* isinstance(e, Exc) on a caught value: the parent chain decides. */
int32_t ps_exc_isa_value(PsExc *e, int32_t wanted) {
    return e != NULL ? ps_exc_isa(e->code, wanted) : 0;
}

PsSeq *ps_exc_args(PsExc *e) {
    int64_t has = e != NULL && e->message.len > 0 ? 1 : 0;
    PsSeq *s = ps_seq_new((int64_t)sizeof(PsBuf), has);
    if (s == NULL) {
        abort();
    }
    if (has) {
        ps_seq_set(s, 0, &e->message);
    }
    return s;
}

__attribute__((noinline, noreturn)) void ps_raise(int32_t code) {
    if (code == 0) {
        code = PS_RUNTIME;
    }
    /* A fresh record every time: reusing the pending one would carry the
       previous message into an exception raised without one. */
    ps_current = ps_exc_new(code);
    ps_deliver();
}

__attribute__((noinline, noreturn)) void ps_raise_code(int32_t code) {
    if (code == 0) {
        code = PS_RUNTIME;
    }
    ps_current = ps_exc_new(code);
    ps_deliver();
}

__attribute__((noinline, noreturn)) void ps_reraise(void) {
    if (ps_current == NULL) {
        ps_current = ps_exc_new(PS_RUNTIME);
    }
    ps_deliver();
}

int32_t ps_exc_code(void) {
    return ps_current != NULL ? ps_current->code : 0;
}

int32_t ps_exc_isa(int32_t raised, int32_t wanted) {
    int32_t code = raised;
    /* The chain is short (a builtin class is at most five deep), but a
       malformed table must not spin: the walk is bounded by the code space. */
    int32_t guard = PS_USER_BASE + PS_USER_EXC_MAX + 1;
    while (code > 0 && guard-- > 0) {
        if (code == wanted) {
            return 1;
        }
        if (code >= PS_USER_BASE) {
            /* A user class: its parent is in the registry. */
            int32_t index = code - PS_USER_BASE;
            if (index >= ps_user_exc_n) {
                return 0;
            }
            code = ps_user_exc[index].parent;
            continue;
        }
        if (code >= PS_RESERVED_BASE) {
            /* A reserved code: queue.Empty and queue.Full are plain
               Exceptions, like every reserved class. */
            code = PS_EXCEPTION;
            continue;
        }
        code = ps_exc_parent[code];
    }
    return 0;
}

/* Register a user exception class: its code, its parent code and its name.
   The emitted module init calls this once per user class, so a user class
   matches `except Exception` and `except <its builtin base>` by walking the
   same parent chain the builtins use. */
void ps_exc_register(int32_t code, int32_t parent, const char *name) {
    if (code < PS_USER_BASE || name == NULL) {
        return;
    }
    int32_t index = code - PS_USER_BASE;
    if (index >= PS_USER_EXC_MAX) {
        return;
    }
    if (index >= ps_user_exc_n) {
        ps_user_exc_n = index + 1;
    }
    ps_user_exc[index].code = code;
    ps_user_exc[index].parent = parent;
    ps_user_exc[index].name = name;
}

int32_t ps_exc_code_by_name(const char *name) {
    for (int32_t i = 0; i < ps_user_exc_n; i++) {
        if (ps_user_exc[i].name != NULL && strcmp(ps_user_exc[i].name, name) == 0) {
            return ps_user_exc[i].code;
        }
    }
    return 0;
}

__attribute__((noreturn)) void ps_raise_cstr(int32_t code, const char *msg) {
    PsExc *e = ps_exc_new(code == 0 ? PS_RUNTIME : code);
    snprintf(e->message.data, 256, "%s", msg);
    e->message.len = (int64_t)strlen(e->message.data);
    ps_current = e;
    ps_deliver();
}

typedef int32_t (*ps_try_fn)(void *);

/* `fn` runs the try body. 0 means the body fell through; 1 means it returned.
The longjmp target is this frame, so `fn` must be called before we return.

The jump buffer is installed with `sigsetjmp(buf, 0)`, not with `setjmp`.
POSIX `setjmp` saves the signal mask on every call, which costs about half a
microsecond; `sigsetjmp` with a savemask of 0 saves no mask and costs a few
nanoseconds. The runtime never blocks or unblocks signals around an unwind, so
the mask save buys nothing and dominated the cost of every `try` body: a
generator loop over ten million items spent five seconds in the mask save
alone. */
__attribute__((noinline)) int32_t ps_try_call(ps_try_fn fn, void *env, int32_t *flow) {
    PsJmp *slot = ps_jmp_free;
    if (slot != NULL) {
        ps_jmp_free = slot->prev;
    } else {
        slot = malloc(sizeof(PsJmp));
        if (slot == NULL) {
            abort();
        }
    }
    slot->prev = ps_top;
    ps_top = slot;
    int code = ps_sigsetjmp(slot->buf, 0);
    if (code != 0) {
        PsJmp *cur = ps_top;
        ps_top = cur->prev;
        cur->prev = ps_jmp_free;
        ps_jmp_free = cur;
        return (int32_t)code;
    }
    int32_t status = fn(env);
    PsJmp *cur = ps_top;
    ps_top = cur->prev;
    cur->prev = ps_jmp_free;
    ps_jmp_free = cur;
    *flow = status;
    return 0;
}

/* Run `fn(env)` and catch the Python-level exception it raises. 0 when the
call returned (its status lands in `*status`), 1 when it raised (`*out` then
holds the record, still the thread's current exception). The event loop uses
this to drive a child coroutine that runs on its own: an exception the child
raises must travel to the awaiting parent, not unwind through the loop's C
frame, where no handler lives and the process would report and exit. The
slot joins the same free list ps_try_call uses, so the cost per poll is the
sigsetjmp alone. */
__attribute__((noinline)) int32_t ps_exc_catch(ps_try_fn fn, void *env, int32_t *status, PsExc **out) {
    PsJmp *slot = ps_jmp_free;
    if (slot != NULL) {
        ps_jmp_free = slot->prev;
    } else {
        slot = malloc(sizeof(PsJmp));
        if (slot == NULL) {
            abort();
        }
    }
    slot->prev = ps_top;
    ps_top = slot;
    int code = ps_sigsetjmp(slot->buf, 0);
    if (code != 0) {
        PsJmp *cur = ps_top;
        ps_top = cur->prev;
        cur->prev = ps_jmp_free;
        ps_jmp_free = cur;
        *out = ps_exc_current();
        return 1;
    }
    *status = fn(env);
    PsJmp *cur = ps_top;
    ps_top = cur->prev;
    cur->prev = ps_jmp_free;
    ps_jmp_free = cur;
    return 0;
}

/* --emit binding: the record a raise left at the C ABI edge, waiting for the
   host to translate it. Per thread, like ps_current and the jump stack. */
static _Thread_local PsExc *ps_bind_parked;

/* Drop the parked record. Nothing else in the runtime points at it: the
   guard clears ps_current when it parks, so the record has exactly one
   owner. cause and args are left alone, like every other allocation the
   runtime never frees. */
static void ps_bind_drop(void) {
    PsExc *e = ps_bind_parked;
    if (e == NULL) {
        return;
    }
    ps_bind_parked = NULL;
    free(e->message.data);
    free(e);
}

int32_t ps_bind_guard(ps_try_fn fn, void *env) {
    int32_t status = 0;
    PsExc *e = NULL;
    /* A record the host never picked up belongs to a call that ran outside
       the generated wrapper; it must not fail this one. */
    ps_bind_drop();
    if (ps_exc_catch(fn, env, &status, &e) != 0) {
        ps_bind_parked = e;
        /* The frame that handled the raise is gone with the call, so the
           thread is not inside an exception any more. A later bare `raise`
           must not reuse the record the host is about to translate. */
        ps_current = NULL;
        return 1;
    }
    return 0;
}

int32_t ps_bind_pending(void) {
    return ps_bind_parked != NULL;
}

const char *ps_bind_name(void) {
    return ps_bind_parked != NULL ? ps_bind_parked->name.data : NULL;
}

const char *ps_bind_message(void) {
    return ps_bind_parked != NULL ? ps_bind_parked->message.data : NULL;
}

void ps_bind_clear(void) {
    ps_bind_drop();
}

void ps_raise_msg(int32_t code, int64_t n, const char *data) {
    if (n < 0) {
        n = 0;
    }
    size_t take = (size_t)n;
    if (take > 255) {
        take = 255;
    }
    PsExc *e = ps_exc_new(code == 0 ? PS_RUNTIME : code);
    memcpy(e->message.data, data, take);
    e->message.data[take] = '\0';
    e->message.len = (int64_t)take;
    if (ps_exc_isa(e->code, PS_SYSTEMEXIT)) {
        e->flags |= PS_EXC_FLAG_EXIT_MSG;
    }
    ps_current = e;
    ps_deliver();
}

/* `raise SystemExit(<int>)` (#603). The record keeps the value as its message,
   so str(e) and a caught `e.args` read the digits CPython reads, and as the
   status the uncaught handler exits with. */
__attribute__((noinline, noreturn)) void ps_raise_system_exit(int32_t code, int64_t status) {
    PsExc *e = ps_exc_new(code == 0 ? PS_SYSTEMEXIT : code);
    snprintf(e->message.data, 256, "%lld", (long long)status);
    e->message.len = (int64_t)strlen(e->message.data);
    e->flags |= PS_EXC_FLAG_EXIT_STATUS;
    e->exit_status = status;
    ps_current = e;
    ps_deliver();
}

void *ps_alloc(int64_t n) {
    if (n < 1) {
        n = 1;
    }
    void *p = calloc(1, (size_t)n);
    if (p == NULL) {
        abort();
    }
    return p;
}

/* The exit registry. The runtime has no refcounts and no GC and the emitted
   IR has no free op, so every sequence and map a program builds lives until
   the process dies. LeakSanitizer flags what is unreachable at exit, and
   whether the last pointer to one of these objects still sits in a register
   or a dead frame's stack slot at the exit fence is timing noise: the same
   binary flapped between a clean run and a ps_seq_new report (11/20 and
   20/20 failure rates in the probe on the dict and nested programs). This
   registry anchors every sequence and map in a global array, so each one is
   reachable at exit by construction and LSan stays silent, without changing
   what a program can observe. Nothing is freed here on purpose: a free would
   orphan the heap cells a sequence's slots point at (the argv string cells
   leaked deterministically the moment the argv list was freed) and could
   race a detached thread still reading the object. */

/* CPython's ZeroDivisionError message names the operation up to 3.13; 3.14
unified them all into "division by zero". The emitted division helpers pass
the variant, and the compiler picks the variant set that matches the running
interpreter (the one the oracle uses). */
__attribute__((noreturn)) void ps_raise_zdi(int32_t variant) {
    const char *msg = NULL;
    switch (variant) {
        case 1:
            msg = "integer division or modulo by zero";
            break;
        case 2:
            msg = "integer modulo by zero";
            break;
        case 3:
            msg = "float division by zero";
            break;
        case 4:
            msg = "float floor division by zero";
            break;
        case 5:
            msg = "float modulo";
            break;
        case 6:
            msg = "0.0 cannot be raised to a negative power";
            break;
        case 7:
            msg = "division by zero";
            break;
        case 8:
            msg = "zero to a negative power";
            break;
        default:
            break;
    }
    if (msg != NULL) {
        ps_raise_cstr(PS_ZERO_DIV, msg);
    }
    ps_raise_code(PS_ZERO_DIV);
}

/* CPython's IndexError message names the container kind. */
static void ps_index_error(const char *what) {
    char msg[64];
    snprintf(msg, sizeof msg, "%s index out of range", what);
    ps_raise_cstr(PS_INDEX, msg);
}

static int64_t ps_index(int64_t len, int64_t i, const char *what) {
    if (i < 0) {
        i += len;
    }
    if (i < 0 || i >= len) {
        ps_index_error(what);
    }
    return i;
}

typedef struct PsOwned {
    void *obj;
    int32_t pad;
} PsOwned;

static PsOwned *ps_owned;
static int64_t ps_owned_len;
static int64_t ps_owned_cap;

static void ps_owned_push(void *obj) {
    if (ps_owned_len == ps_owned_cap) {
        int64_t cap = ps_owned_cap == 0 ? 64 : ps_owned_cap * 2;
        PsOwned *grown = realloc(ps_owned, (size_t)cap * sizeof *grown);
        if (grown == NULL) {
            abort();
        }
        ps_owned = grown;
        ps_owned_cap = cap;
    }
    ps_owned[ps_owned_len].obj = obj;
    ps_owned[ps_owned_len].pad = 0;
    ps_owned_len += 1;
}

PsSeq *ps_seq_new(int64_t elem, int64_t n) {
    if (elem <= 0 || n < 0) {
        ps_raise_code(PS_OVERFLOW);
    }
    if (n > 0 && (uint64_t)n > UINT64_MAX / (uint64_t)elem) {
        ps_raise_code(PS_OVERFLOW);
    }
    PsSeq *s = calloc(1, sizeof(PsSeq));
    if (s == NULL) {
        abort();
    }
    ps_owned_push(s);
    s->len = n;
    s->elem = elem;
    s->cap = n;
    if (n == 0) {
        s->data = NULL;
        return s;
    }
    s->data = calloc((size_t)n, (size_t)elem);
    if (s->data == NULL) {
        abort();
    }
    return s;
}

int64_t ps_seq_len(PsSeq *s) { return s->len; }

void ps_seq_get(PsSeq *s, int64_t i, void *out) {
    i = ps_index(s->len, i, "list");
    memcpy(out, (char *)s->data + (size_t)i * (size_t)s->elem, (size_t)s->elem);
}

/* The membership index of a sequence used as a set (#382). A set is a
   sequence, and `s.add(x)` lowers to a containment test plus a push, so
   the linear `ps_seq_has_*` scan made every add O(n). The index is a
   hash table over the cells, built once the sequence is long enough for
   the linear scan to dominate (PS_IDX_MIN, so short sequences never pay
   for it), and dropped by any mutation that moves cells around. */

#define PS_IDX_MIN 32
static uint64_t ps_seq_mix64(uint64_t x) {
    x += 0x9E3779B97F4A7C15ULL;
    x = (x ^ (x >> 30)) * 0xBF58476D1CE4E5B9ULL;
    x = (x ^ (x >> 27)) * 0x94D049BB133111EBULL;
    return x ^ (x >> 31);
}

static int64_t ps_seq_cell_hash(PsSeq *s, int32_t kind, const void *cell) {
    if (kind == 1) {
        int64_t k = 0;
        memcpy(&k, cell, 8);
        return (int64_t)ps_seq_mix64((uint64_t)k);
    }
    PsBuf b;
    memcpy(&b, cell, sizeof b);
    uint64_t h = 0xCBF29CE484222325ULL;
    for (int64_t i = 0; i < b.len; i++) {
        h ^= (unsigned char)b.data[i];
        h *= 0x100000001B3ULL;
    }
    return (int64_t)ps_seq_mix64(h);
}

static int ps_seq_cell_eq(PsSeq *s, int32_t kind, const void *cell, const void *key) {
    if (kind == 1) {
        return memcmp(cell, key, 8) == 0;
    }
    PsBuf a;
    PsBuf b;
    memcpy(&a, cell, sizeof a);
    memcpy(&b, key, sizeof b);
    return a.len == b.len && (a.len == 0 || memcmp(a.data, b.data, (size_t)a.len) == 0);
}

static int64_t ps_seq_idx_count_for(int64_t len) {
    int64_t want = len * 2;
    int64_t n = 64;
    while (n < want) {
        n *= 2;
    }
    return n;
}

/* Build a fresh probe table of `n` slots (a power of two) from the cells.
   An indexed push grows the table by doubling it rather than re-deriving its
   size from the length: deriving it from the length lands the new table at
   exactly load one half, so the next push overflows it again and rebuilds a
   second time, which doubles the rehash work of every doubling. */
static void ps_seq_index_build_n(PsSeq *s, int64_t n) {
    int64_t cells = s->cap > s->len ? s->cap : (s->len > 0 ? s->len : 1);
    int32_t *slots = calloc((size_t)n, sizeof(int32_t));
    int64_t *hashes = malloc((size_t)cells * sizeof(int64_t));
    if (slots == NULL || hashes == NULL) {
        abort();
    }
    free(s->idx_slots);
    free(s->idx_hashes);
    s->idx_slots = slots;
    s->idx_hashes = hashes;
    s->idx_mask = (int32_t)(n - 1);
    s->idx_count = n;
    s->idx_kind = 0;
    for (int64_t i = 0; i < s->len; i++) {
        void *cell = (char *)s->data + (size_t)i * (size_t)s->elem;
        int32_t kind = s->elem == 8 ? 1 : 2;
        int64_t h = ps_seq_cell_hash(s, kind, cell);
        s->idx_hashes[i] = h;
        int64_t at = (uint64_t)h & (uint64_t)s->idx_mask;
        while (s->idx_slots[at] != 0) {
            at = (at + 1) & s->idx_mask;
        }
        s->idx_slots[at] = (int32_t)(i + 1);
        if (s->idx_kind == 0) {
            s->idx_kind = kind;
        }
    }
    if (s->idx_kind == 0) {
        s->idx_kind = s->elem == 8 ? 1 : 2;
    }
}

static void ps_seq_index_build(PsSeq *s) {
    ps_seq_index_build_n(s, ps_seq_idx_count_for(s->len > 0 ? s->len : 1));
}

/* Build the index when the caller asks for the kind the cells carry and the
   sequence is long enough to pay for it. Returns 1 when the index is live. */
static int ps_seq_index_load(PsSeq *s, int32_t kind) {
    if (s->idx_slots != NULL) {
        return s->idx_kind == kind;
    }
    if (s->len < PS_IDX_MIN || (s->elem != 8 && s->elem != 16)) {
        return 0;
    }
    ps_seq_index_build(s);
    return s->idx_kind == kind;
}

/* The cell holding key, or -1 when it is absent. */
static int64_t ps_seq_index_hit(PsSeq *s, const void *key) {
    if (s->idx_slots == NULL || s->len == 0) {
        return -1;
    }
    int64_t h = ps_seq_cell_hash(s, s->idx_kind, key);
    int64_t at = (uint64_t)h & (uint64_t)s->idx_mask;
    while (s->idx_slots[at] != 0) {
        int32_t cell = s->idx_slots[at];
        if (cell > 0 && cell <= s->len) {
            void *p = (char *)s->data + (size_t)(cell - 1) * (size_t)s->elem;
            if (s->idx_hashes[cell - 1] == h && ps_seq_cell_eq(s, s->idx_kind, p, key)) {
                return cell - 1;
            }
        }
        at = (at + 1) & s->idx_mask;
    }
    return -1;
}

static void ps_seq_index_drop(PsSeq *s) {
    free(s->idx_slots);
    free(s->idx_hashes);
    s->idx_slots = NULL;
    s->idx_hashes = NULL;
    s->idx_mask = 0;
    s->idx_count = 0;
    s->idx_kind = 0;
}

/* Drop the membership index: a mutation that moves cells around (an
   insert, a remove, a splice, a sort, a reverse) invalidates it. The next
   containment test rebuilds it from the cells. */
void ps_seq_index_invalidate(PsSeq *s) { ps_seq_index_drop(s); }

/* Grow the hash cache along with the data array, so a push into a live
   index never writes past the cache. */
void ps_seq_index_reserve(PsSeq *s, int64_t cap) {
    if (s->idx_slots == NULL || s->idx_hashes == NULL) {
        return;
    }
    int64_t *hashes = realloc(s->idx_hashes, (size_t)cap * sizeof(int64_t));
    if (hashes == NULL) {
        abort();
    }
    s->idx_hashes = hashes;
}

/* A push keeps a live index in sync when the table still has room; the
   table grows through a rebuild like the map index does. The hash cache
   rides the same capacity as the data array (ps_seq_gap grows it). */
static void ps_seq_index_add(PsSeq *s) {
    if (s->idx_slots == NULL || s->idx_hashes == NULL) {
        return;
    }
    if ((s->len + 1) * 2 > s->idx_count) {
        ps_seq_index_build_n(s, s->idx_count > 0 ? s->idx_count * 2 : 64);
        return;
    }
    void *cell = (char *)s->data + (size_t)(s->len - 1) * (size_t)s->elem;
    int64_t h = ps_seq_cell_hash(s, s->idx_kind, cell);
    s->idx_hashes[s->len - 1] = h;
    int64_t at = (uint64_t)h & (uint64_t)s->idx_mask;
    while (s->idx_slots[at] != 0) {
        at = (at + 1) & s->idx_mask;
    }
    s->idx_slots[at] = (int32_t)s->len;
}

void ps_seq_push(PsSeq *s, const void *src) {
    if (s->len == s->cap) {
        int64_t cap = s->cap == 0 ? 4 : s->cap * 2;
        if (cap < s->len + 1) {
            ps_raise_code(PS_OVERFLOW);
        }
        if ((uint64_t)cap > UINT64_MAX / (uint64_t)s->elem) {
            ps_raise_code(PS_OVERFLOW);
        }
        void *data = realloc(s->data, (size_t)cap * (size_t)s->elem);
        if (data == NULL) {
            abort();
        }
        s->data = data;
        s->cap = cap;
        ps_seq_index_reserve(s, cap);
    }
    memcpy((char *)s->data + (size_t)s->len * (size_t)s->elem, src, (size_t)s->elem);
    s->len += 1;
    ps_seq_index_add(s);
}

void ps_seq_set(PsSeq *s, int64_t i, void *src) {
    i = ps_index(s->len, i, "list");
    memcpy((char *)s->data + (size_t)i * (size_t)s->elem, src, (size_t)s->elem);
    ps_seq_index_invalidate(s);
}

static int ps_buf_eq(PsBuf a, PsBuf b) {
    if (a.len != b.len) {
        return 0;
    }
    if (a.len == 0) {
        return 1;
    }
    return memcmp(a.data, b.data, (size_t)a.len) == 0;
}

int32_t ps_seq_has_i64(PsSeq *s, int64_t needle) {
    if (ps_seq_index_load(s, 1) == 0) {
        for (int64_t i = 0; i < s->len; i++) {
            int64_t cur = 0;
            memcpy(&cur, (char *)s->data + (size_t)i * (size_t)s->elem, 8);
            if (cur == needle) {
                return 1;
            }
        }
        return 0;
    }
    return ps_seq_index_hit(s, &needle) >= 0;
}

int32_t ps_seq_has_f64(PsSeq *s, double needle) {
    for (int64_t i = 0; i < s->len; i++) {
        double cur = 0;
        memcpy(&cur, (char *)s->data + (size_t)i * (size_t)s->elem, 8);
        if (cur == needle) {
            return 1;
        }
    }
    return 0;
}

int32_t ps_seq_has_buf(PsSeq *s, int64_t n, char *p) {
    PsBuf needle = {n, p};
    if (ps_seq_index_load(s, 2) == 0) {
        for (int64_t i = 0; i < s->len; i++) {
            PsBuf cur;
            memcpy(&cur, (char *)s->data + (size_t)i * (size_t)s->elem, 16);
            if (ps_buf_eq(cur, needle)) {
                return 1;
            }
        }
        return 0;
    }
    return ps_seq_index_hit(s, &needle) >= 0;
}

/* Splitmix64 for int keys and FNV-1a for str keys: the map's key hashes.
   Both mix all input bits, so sequential int keys land on scattered slots
   and a str key uses every byte. */
static uint64_t ps_mix64(uint64_t x) {
    x += 0x9E3779B97F4A7C15ULL;
    x = (x ^ (x >> 30)) * 0xBF58476D1CE4E5B9ULL;
    x = (x ^ (x >> 27)) * 0x94D049BB133111EBULL;
    return x ^ (x >> 31);
}

static int64_t ps_key_hash(PsMap *m, const void *key) {
    if (m->key_kind == 1) {
        int64_t k = 0;
        memcpy(&k, key, 8);
        return (int64_t)ps_mix64((uint64_t)k);
    }
    PsBuf b;
    memcpy(&b, key, sizeof b);
    uint64_t h = 0xCBF29CE484222325ULL;
    for (int64_t i = 0; i < b.len; i++) {
        h ^= (unsigned char)b.data[i];
        h *= 0x100000001B3ULL;
    }
    return (int64_t)ps_mix64(h);
}

#define PS_DEAD_HASH ((int64_t)INT64_MIN)

/* The probe table is a power of two with two slots per dense row, so the
   load never passes one half and a probe always ends on an empty slot. */
static int64_t ps_slot_count(int64_t cap) {
    int64_t want = cap * 2;
    int64_t n = 16;
    while (n < want) {
        n *= 2;
    }
    return n;
}

/* Rebuild the probe table from the cached hashes. The hashes live in their
   own array, so a rebuild (a bigger table, or a table full of tombstones)
   never re-hashes a string. */
static void ps_index_build(PsMap *m) {
    int64_t n = ps_slot_count(m->cap);
    int32_t *slots = calloc((size_t)n, sizeof(int32_t));
    if (slots == NULL) {
        abort();
    }
    free(m->slots);
    m->slots = slots;
    m->slot_count = n;
    m->slot_mask = (int32_t)(n - 1);
    m->tombs = 0;
    for (int64_t i = 0; i < m->rows; i++) {
        if (m->hashes[i] == PS_DEAD_HASH) {
            continue;
        }
        int64_t at = (uint64_t)m->hashes[i] & (uint64_t)m->slot_mask;
        while (m->slots[at] != 0) {
            at = (at + 1) & m->slot_mask;
        }
        m->slots[at] = (int32_t)(i + 1);
    }
}

PsMap *ps_map_new(int32_t key_kind, int64_t key_size, int64_t val_size, int64_t cap) {
    if (cap < 0 || key_size <= 0 || val_size <= 0) {
        ps_raise_code(PS_OVERFLOW);
    }
    PsMap *m = calloc(1, sizeof(PsMap));
    if (m == NULL) {
        abort();
    }
    ps_owned_push(m);
    m->key_kind = key_kind;
    m->key_size = key_size;
    m->val_size = val_size;
    m->cap = cap;
    m->len = 0;
    m->rows = 0;
    if (cap > 0) {
        m->keys = calloc((size_t)cap, (size_t)key_size);
        m->vals = calloc((size_t)cap, (size_t)val_size);
        m->hashes = calloc((size_t)cap, sizeof(int64_t));
        m->order = calloc((size_t)cap, sizeof(int32_t));
        if (m->keys == NULL || m->vals == NULL || m->hashes == NULL || m->order == NULL) {
            abort();
        }
        ps_index_build(m);
    }
    return m;
}

int64_t ps_map_len(PsMap *m) { return m->len; }

static int ps_key_eq(PsMap *m, const void *a, const void *b) {
    if (m->key_kind == 1) {
        int64_t ka = 0;
        int64_t kb = 0;
        memcpy(&ka, a, 8);
        memcpy(&kb, b, 8);
        return ka == kb;
    }
    PsBuf ba;
    PsBuf bb;
    memcpy(&ba, a, 16);
    memcpy(&bb, b, 16);
    return ps_buf_eq(ba, bb);
}

/* The dense row holding key, or -1 when it is absent. The probe walks live
   keys and tombstones until an empty slot ends the chain; a hit compares
   the cached hash first, then the key bytes, so a miss on a str key never
   re-reads the string. */
static int64_t ps_map_find(PsMap *m, const void *key) {
    if (m->slot_mask == 0 || m->len == 0) {
        return -1;
    }
    int64_t h = ps_key_hash(m, key);
    int64_t at = (uint64_t)h & (uint64_t)m->slot_mask;
    while (m->slots[at] != 0) {
        int32_t cell = m->slots[at];
        if (cell > 0) {
            int64_t i = cell - 1;
            if (m->hashes[i] == h && ps_key_eq(m, (char *)m->keys + (size_t)i * (size_t)m->key_size, key)) {
                return i;
            }
        }
        at = (at + 1) & m->slot_mask;
    }
    return -1;
}

static void ps_map_grow(PsMap *m) {
    int64_t cap = m->cap == 0 ? 4 : m->cap * 2;
    if (cap < m->rows + 1) {
        ps_raise_code(PS_OVERFLOW);
    }
    if ((uint64_t)cap > UINT64_MAX / (uint64_t)m->key_size || (uint64_t)cap > UINT64_MAX / (uint64_t)m->val_size) {
        ps_raise_code(PS_OVERFLOW);
    }
    void *keys = realloc(m->keys, (size_t)cap * (size_t)m->key_size);
    void *vals = realloc(m->vals, (size_t)cap * (size_t)m->val_size);
    int64_t *hashes = realloc(m->hashes, (size_t)cap * sizeof(int64_t));
    int32_t *order = realloc(m->order, (size_t)cap * sizeof(int32_t));
    if (keys == NULL || vals == NULL || hashes == NULL || order == NULL) {
        abort();
    }
    m->keys = keys;
    m->vals = vals;
    m->hashes = hashes;
    m->order = order;
    m->cap = cap;
    ps_index_build(m);
}

void ps_map_put(PsMap *m, void *key, void *val) {
    if (m->slot_mask == 0) {
        ps_index_build(m);
    }
    int64_t h = ps_key_hash(m, key);
    int64_t at = (uint64_t)h & (uint64_t)m->slot_mask;
    while (m->slots[at] != 0) {
        int32_t cell = m->slots[at];
        if (cell > 0) {
            int64_t i = cell - 1;
            if (m->hashes[i] == h && ps_key_eq(m, (char *)m->keys + (size_t)i * (size_t)m->key_size, key)) {
                memcpy((char *)m->vals + (size_t)i * (size_t)m->val_size, val, (size_t)m->val_size);
                return;
            }
        }
        at = (at + 1) & m->slot_mask;
    }
    if (m->rows == m->cap) {
        ps_map_grow(m);
    }
    /* Delete-heavy workloads fill the table with tombstones; rebuild when
       they pass a quarter of the table so the probes stay short. */
    if (m->tombs * 4 >= m->slot_count) {
        ps_index_build(m);
    }
    at = (uint64_t)h & (uint64_t)m->slot_mask;
    while (m->slots[at] != 0) {
        at = (at + 1) & m->slot_mask;
    }
    int64_t i = m->rows;
    m->rows += 1;
    m->len += 1;
    memcpy((char *)m->keys + (size_t)i * (size_t)m->key_size, key, (size_t)m->key_size);
    m->hashes[i] = h;
    m->slots[at] = (int32_t)(i + 1);
    /* The order cache holds the live rows in insertion order. It is
       appendable while it is in sync; a delete leaves a hole in it, so the
       next iteration rebuilds it from the dense rows. */
    if (!m->order_dirty && m->order_len < m->cap) {
        m->order[m->order_len] = (int32_t)i;
        m->order_len += 1;
    } else {
        m->order_dirty = 1;
    }
    memcpy((char *)m->vals + (size_t)i * (size_t)m->val_size, val, (size_t)m->val_size);
}

void ps_map_get(PsMap *m, void *key, void *out) {
    int64_t at = ps_map_find(m, key);
    if (at < 0) {
        /* CPython's KeyError message is the key's repr: 'missing' or 42. */
        char msg[280];
        if (m->key_kind == 1) {
            snprintf(msg, sizeof msg, "%lld", (long long)*(int64_t *)key);
        } else {
            PsBuf *k = key;
            PsGrow g;
            PsBuf quoted;
            ps_grow_init(&g);
            ps_report_quoted(&g, k);
            ps_grow_finish(&g, &quoted);
            snprintf(msg, sizeof msg, "%.*s", (int)(quoted.len < 250 ? quoted.len : 250), quoted.data);
            free(quoted.data);
        }
        PsExc *e = ps_exc_new(PS_KEY);
        snprintf(e->message.data, 256, "%s", msg);
        e->message.len = (int64_t)strlen(e->message.data);
        e->flags = PS_EXC_FLAG_PREQUOTED;
        ps_current = e;
        ps_deliver();
    }
    memcpy(out, (char *)m->vals + (size_t)at * (size_t)m->val_size, (size_t)m->val_size);
}

int32_t ps_map_has(PsMap *m, void *key) { return ps_map_find(m, key) >= 0; }

/* Delete the key the probe finds: the dense row is marked dead and its slot
   becomes a tombstone, so the probe chain of the other keys stays intact and
   the survivors keep their insertion order. The order cache is stale after
   this, and ps_map_row_at rebuilds it on the next iteration. */
int64_t ps_map_delete(PsMap *m, void *key) {
    int64_t at = ps_map_find(m, key);
    if (at < 0) {
        return -1;
    }
    int64_t h = m->hashes[at];
    int64_t slot = (uint64_t)h & (uint64_t)m->slot_mask;
    while (m->slots[slot] != 0) {
        if (m->slots[slot] > 0 && m->slots[slot] - 1 == at) {
            m->slots[slot] = -1;
            m->tombs += 1;
            break;
        }
        slot = (slot + 1) & m->slot_mask;
    }
    m->hashes[at] = PS_DEAD_HASH;
    m->len -= 1;
    m->order_dirty = 1;
    return at;
}

/* Drop every key: the probe table is emptied and the dense rows are
   released, so the map is empty but keeps its capacity. */
void ps_map_reset(PsMap *m) {
    if (m->slot_count > 0) {
        memset(m->slots, 0, (size_t)m->slot_count * sizeof(int32_t));
    }
    m->tombs = 0;
    m->len = 0;
    m->rows = 0;
    m->order_len = 0;
    m->order_dirty = 0;
}

/* The dense row of the live key at iteration position p: order[p] when the
   order cache is current, else a rebuild first. Callers walk positions 0
   .. len-1, so one rebuild serves a whole iteration. */
static int64_t ps_map_row_at(PsMap *m, int64_t p) {
    if (p < 0 || p >= m->len) {
        return -1;
    }
    if (m->order == NULL || m->order_len < m->len) {
        m->order_dirty = 1;
    }
    if (m->order_dirty) {
        int64_t live = 0;
        for (int64_t r = 0; r < m->rows && live < m->len; r++) {
            if (m->hashes[r] != PS_DEAD_HASH) {
                m->order[live] = (int32_t)r;
                live += 1;
            }
        }
        m->order_len = live;
        m->order_dirty = 0;
    }
    return m->order[p];
}

void ps_map_key_at(PsMap *m, int64_t i, void *out) {
    int64_t r = ps_map_row_at(m, i);
    if (r < 0) {
        ps_raise_code(PS_INDEX);
    }
    memcpy(out, (char *)m->keys + (size_t)r * (size_t)m->key_size, (size_t)m->key_size);
}

PsSeq *ps_dict_keys(PsMap *m) {
    PsSeq *seq = ps_seq_new(m->key_size, m->len);
    for (int64_t p = 0; p < m->len; p++) {
        int64_t r = ps_map_row_at(m, p);
        ps_seq_set(seq, p, (char *)m->keys + (size_t)r * (size_t)m->key_size);
    }
    return seq;
}

void ps_map_val_at(PsMap *m, int64_t i, void *out) {
    int64_t r = ps_map_row_at(m, i);
    if (r < 0) {
        ps_raise_code(PS_INDEX);
    }
    memcpy(out, (char *)m->vals + (size_t)r * (size_t)m->val_size, (size_t)m->val_size);
}

static int ps_space(unsigned char c) {
    return c == ' ' || c == '\t' || c == '\n' || c == '\r' || c == '\v' || c == '\f' || c == 0x1c ||
           c == 0x1d || c == 0x1e || c == 0x1f;
}

static void ps_view(PsBuf *out, const char *p, int64_t n) {
    out->len = n;
    out->data = n == 0 ? &ps_empty_byte : (char *)p;
}

void ps_buf_view(PsBuf *out, const char *p, int64_t n) { ps_view(out, p, n); }

void ps_buf_copy(PsBuf *out, const char *p, int64_t n) {
    if (n < 0) {
        ps_raise_code(PS_OVERFLOW);
    }
    char *q = malloc((size_t)n + 1);
    if (q == NULL) {
        abort();
    }
    if (n > 0) {
        memcpy(q, p, (size_t)n);
    }
    q[n] = '\0';
    out->len = n;
    out->data = q;
}

void ps_str_counts(int64_t n, const char *p, int64_t *nchars, int32_t *flags) {
    /* One pass over the UTF-8 bytes: the code-point count and the ASCII
       flag. A continuation byte (10xxxxxx) never starts a code point, so
       the count is the number of bytes that are not continuations. The
       input is a well-formed UTF-8 buffer by construction (every producer
       encodes through ps_utf8_encode or the compiler's literal path), so
       the walk does not re-validate; ps_utf8_decode owns rejection. */
    int64_t chars = 0;
    int32_t ascii = 1;
    for (int64_t i = 0; i < n; i++) {
        unsigned char c = (unsigned char)p[i];
        if ((c & 0xC0) != 0x80) {
            chars++;
        }
        if (c >= 0x80) {
            ascii = 0;
        }
    }
    if (nchars != NULL) {
        *nchars = chars;
    }
    if (flags != NULL) {
        *flags = ascii ? PS_STR_FLAG_ASCII : 0;
    }
}

void ps_str_header(int64_t nbytes, const char *p, int64_t *nchars, int32_t *flags, PsBuf *out) {
    out->len = nbytes;
    out->data = (char *)p;
    ps_str_counts(nbytes, p, nchars, flags);
}

/* ---- UTF-8 cursor helpers (S2, issue #474) ----

A code point is 1 to 4 bytes. The helpers below walk and measure UTF-8 on
buffers the producers already validated (the compiler's literal path and
ps_utf8_decode both reject malformed input), so the fast paths do not
re-check every byte. The ASCII flag makes every ASCII string O(1): the
code-point index is the byte offset. */

int ps_utf8_len(unsigned char c) {
    if (c < 0x80) {
        return 1;
    }
    if ((c & 0xE0) == 0xC0) {
        return 2;
    }
    if ((c & 0xF0) == 0xE0) {
        return 3;
    }
    return 4;
}

/* The byte offset of code point `i` (negative wraps like ps_index), or -1
   when `i` is out of range. A negative index needs the full count first, so
   it pays the same one pass the count itself costs. */
int64_t ps_str_offset(int64_t n, const char *p, int64_t i) {
    int64_t at = 0;
    int64_t seen = 0;
    if (i < 0) {
        int64_t chars = 0;
        for (int64_t k = 0; k < n; k++) {
            chars += ((unsigned char)p[k] & 0xC0) != 0x80;
        }
        i += chars;
        if (i < 0) {
            return -1;
        }
    }
    while (at < n && seen < i) {
        at += ps_utf8_len((unsigned char)p[at]);
        seen++;
    }
    return seen == i ? at : -1;
}

/* The byte width of the code point at `at` (a lead byte position). */
static int64_t ps_str_char_len(int64_t n, const char *p, int64_t at) {
    int w = ps_utf8_len((unsigned char)p[at]);
    return at + w <= n ? w : n - at;
}

/* The ASCII flag decides the fast path; a buffer whose bytes are all below
   0x80 is the same question the flag answers for well-formed input. */
static int ps_all_ascii(int64_t n, const char *p) {
    for (int64_t i = 0; i < n; i++) {
        if ((unsigned char)p[i] >= 0x80) {
            return 0;
        }
    }
    return 1;
}

/* The code-point length of the buffer. */
int64_t ps_str_nchars(int64_t n, const char *p) {
    int64_t chars = 0;
    for (int64_t k = 0; k < n; k++) {
        chars += ((unsigned char)p[k] & 0xC0) != 0x80;
    }
    return chars;
}

void ps_grow_init(PsGrow *g) {
    g->data = NULL;
    g->len = 0;
    g->cap = 0;
}

void ps_grow_add(PsGrow *g, const char *p, int64_t n) {
    if (n < 0) {
        ps_raise_code(PS_OVERFLOW);
    }
    if (n > INT64_MAX - g->len - 1) {
        ps_raise_code(PS_OVERFLOW);
    }
    if (g->len + n + 1 > g->cap) {
        int64_t cap = g->cap == 0 ? 32 : g->cap;
        while (cap < g->len + n + 1) {
            if (cap > INT64_MAX / 2) {
                ps_raise_code(PS_OVERFLOW);
            }
            cap *= 2;
        }
        char *data = realloc(g->data, (size_t)cap);
        if (data == NULL) {
            abort();
        }
        g->data = data;
        g->cap = cap;
    }
    if (n > 0) {
        memcpy(g->data + g->len, p, (size_t)n);
    }
    g->len += n;
    g->data[g->len] = '\0';
}

void ps_grow_byte(PsGrow *g, unsigned char c) {
    char ch = (char)c;
    ps_grow_add(g, &ch, 1);
}

void ps_grow_finish(PsGrow *g, PsBuf *out) {
    if (g->data == NULL) {
        ps_buf_copy(out, "", 0);
        return;
    }
    out->len = g->len;
    out->data = g->data;
    g->data = NULL;
    g->len = 0;
    g->cap = 0;
}

void ps_str_get(int64_t n, char *p, int64_t i, PsBuf *out) {
    /* s[i] is the i-th code point: a one-byte view on an ASCII string (the
       byte offset is the index), the scanned offset otherwise. */
    if (ps_all_ascii(n, p)) {
        i = ps_index(n, i, "string");
        ps_view(out, p + i, 1);
        return;
    }
    int64_t at = ps_str_offset(n, p, i);
    if (at < 0) {
        ps_index(ps_str_nchars(n, p), i, "string");
    }
    ps_view(out, p + at, ps_str_char_len(n, p, at));
}

int64_t ps_buf_at(int64_t n, char *p, int64_t i) {
    i = ps_index(n, i, "string");
    return (unsigned char)p[i];
}

static void ps_trim(int64_t n, const char *p, int which, PsBuf *out) {
    int64_t i = 0;
    int64_t j = n;
    if (which != 2) {
        while (i < j && ps_space((unsigned char)p[i])) {
            i++;
        }
    }
    if (which != 1) {
        while (j > i && ps_space((unsigned char)p[j - 1])) {
            j--;
        }
    }
    ps_view(out, p + i, j - i);
}

void ps_str_strip(int64_t n, char *p, PsBuf *out) { ps_trim(n, p, 0, out); }
void ps_str_lstrip(int64_t n, char *p, PsBuf *out) { ps_trim(n, p, 1, out); }
void ps_str_rstrip(int64_t n, char *p, PsBuf *out) { ps_trim(n, p, 2, out); }

int32_t ps_str_startswith(int64_t n, char *p, int64_t pn, char *pp) {
    if (pn > n) {
        return 0;
    }
    if (pn == 0) {
        return 1;
    }
    return memcmp(p, pp, (size_t)pn) == 0;
}

int32_t ps_str_endswith(int64_t n, char *p, int64_t pn, char *pp) {
    if (pn > n) {
        return 0;
    }
    if (pn == 0) {
        return 1;
    }
    return memcmp(p + (n - pn), pp, (size_t)pn) == 0;
}

int64_t ps_str_find(int64_t n, char *p, int64_t pn, char *pp) {
    /* find over code points: the needle's UTF-8 bytes match at exactly one
       code-point boundary (UTF-8 is self-synchronizing, so a byte match
       inside a multi-byte code point cannot happen unless the offsets agree
       with a code-point boundary). The returned offset is already a
       code-point index only on ASCII; the general case counts the code
       points before the match. */
    if (pn == 0) {
        return 0;
    }
    if (pn > n) {
        return -1;
    }
    for (int64_t i = 0; i + pn <= n; i++) {
        if (memcmp(p + i, pp, (size_t)pn) == 0) {
            if (ps_all_ascii(n, p)) {
                return i;
            }
            return ps_str_nchars(i, p);
        }
    }
    return -1;
}

int32_t ps_str_eq(int64_t an, char *ap, int64_t bn, char *bp) {
    /* Code-point equality is byte equality: UTF-8 is a bijection between
       code-point sequences and byte sequences, so no decode is needed. */
    PsBuf a = {an, ap};
    PsBuf b = {bn, bp};
    return ps_buf_eq(a, b);
}

int32_t ps_str_cmp(int64_t an, char *ap, int64_t bn, char *bp) {
    /* Code-point ordering is byte ordering: UTF-8 preserves code-point
       order, so a memcmp with a length tiebreak is CPython's `<`. */
    int64_t n = an < bn ? an : bn;
    int cmp = n == 0 ? 0 : memcmp(ap, bp, (size_t)n);
    if (cmp < 0) {
        return -1;
    }
    if (cmp > 0) {
        return 1;
    }
    if (an < bn) {
        return -1;
    }
    if (an > bn) {
        return 1;
    }
    return 0;
}

/* The case family. Each function walks code points; the ASCII bytes keep
   the old branch (no table hit), everything else looks up ps_case_map
   (Unicode 16.0.0, generated by scripts/gen_case_tables.py). The full mappings
   match CPython: an expansion (upper of the eszett, fold of the same,
   lower of the dotted I) emits every code point of the mapping. */

static const PsCaseRec *ps_case_find(uint32_t cp) {
    int64_t lo = 0;
    int64_t hi = PS_CASE_MAP_N;
    while (lo < hi) {
        int64_t mid = lo + (hi - lo) / 2;
        if (ps_case_map[mid].cp < cp) {
            lo = mid + 1;
        } else {
            hi = mid;
        }
    }
    if (lo < PS_CASE_MAP_N && ps_case_map[lo].cp == cp) {
        return &ps_case_map[lo];
    }
    return NULL;
}

static const char *ps_case_exp(const PsCaseExp *table, int64_t n, uint32_t cp, uint32_t *len) {
    int64_t lo = 0;
    int64_t hi = n;
    while (lo < hi) {
        int64_t mid = lo + (hi - lo) / 2;
        if (table[mid].cp < cp) {
            lo = mid + 1;
        } else {
            hi = mid;
        }
    }
    if (lo < n && table[lo].cp == cp) {
        *len = table[lo].len;
        return ps_case_pool + table[lo].off;
    }
    *len = 0;
    return NULL;
}

typedef enum {
    PS_CASE_TO_UPPER = 0,
    PS_CASE_TO_LOWER = 1,
    PS_CASE_TO_FOLD = 2,
    PS_CASE_TO_TITLE = 3
} PsCaseKind;

/* The Cased / Case_Ignorable properties from the generated tables. */
static int ps_case_cased(uint32_t cp) {
    if (cp < 0x80) {
        return (cp >= 'a' && cp <= 'z') || (cp >= 'A' && cp <= 'Z');
    }
    const PsCaseRec *rec = ps_case_find(cp);
    return rec != NULL && (rec->flags & PS_CASE_F_CASED) != 0;
}

/* The Cased property for callers outside ps_rt.c (the S3 predicates in
   ps_more.c): shared with the case family so the property has one owner. */
int ps_str_cased(uint32_t cp) {
    return ps_case_cased(cp);
}

/* Whether the upper/lower mapping changes this code point, for callers
   outside ps_rt.c (the S3 predicates). The multi-character expansions (the
   eszett's upper is "SS") count as a change: CPython's isupper/islower
   follow the Unicode category (Lu and Lt are upper, Ll is lower), and an
   expansion source is by definition not its own opposite case (the eszett
   is Ll, the titlecase digraphs are Lt). */
int ps_str_upper_moves(uint32_t cp) {
    const PsCaseRec *rec = ps_case_find(cp);
    return rec != NULL && rec->up != cp;
}

int ps_str_lower_moves(uint32_t cp) {
    const PsCaseRec *rec = ps_case_find(cp);
    return rec != NULL && rec->lo != cp;
}

static int ps_case_ignorable(uint32_t cp) {
    if (cp < 0x80) {
        return cp == 0x27 || cp == 0x2E || cp == 0x3A || cp == 0x5E || cp == 0x60;
    }
    const PsCaseRec *rec = ps_case_find(cp);
    return rec != NULL && (rec->flags & PS_CASE_F_IGNORABLE) != 0;
}

/* The Final_Sigma context: the sigma at `cps[at]` is final when preceded by
   a cased code point (skipping case-ignorables backwards) and not followed
   by one (skipping case-ignorables forwards, end of string counts as
   not-cased). */
static int ps_sigma_final(const uint32_t *cps, int64_t n, int64_t at) {
    int64_t j = at - 1;
    while (j >= 0 && ps_case_ignorable(cps[j])) {
        j--;
    }
    if (j < 0 || !ps_case_cased(cps[j])) {
        return 0;
    }
    int64_t k = at + 1;
    while (k < n && ps_case_ignorable(cps[k])) {
        k++;
    }
    return k >= n || !ps_case_cased(cps[k]);
}

/* Decode the code point starting at p[at] (n is the byte length). Writes
   the code point and its width; invalid UTF-8 cannot reach the case
   functions (the decoder validated the bytes). */
static void ps_case_decode(int64_t n, const char *p, int64_t at, uint32_t *cp, int *w) {
    unsigned char c = (unsigned char)p[at];
    if (c < 0x80) {
        *cp = c;
        *w = 1;
        return;
    }
    int width = ps_utf8_len(c);
    uint32_t v = 0;
    switch (width) {
    case 2:
        v = (uint32_t)(c & 0x1F);
        break;
    case 3:
        v = (uint32_t)(c & 0x0F);
        break;
    default:
        v = (uint32_t)(c & 0x07);
        break;
    }
    for (int k = 1; k < width && at + k < n; k++) {
        v = (v << 6) | (uint32_t)((unsigned char)p[at + k] & 0x3F);
    }
    *cp = v;
    *w = width;
}

/* Emit one code point as UTF-8. */
static void ps_case_emit_cp(PsGrow *g, uint32_t want) {
    if (want < 0x80) {
        ps_grow_byte(g, (unsigned char)want);
    } else if (want < 0x800) {
        ps_grow_byte(g, (unsigned char)(0xC0 | (want >> 6)));
        ps_grow_byte(g, (unsigned char)(0x80 | (want & 0x3F)));
    } else if (want < 0x10000) {
        ps_grow_byte(g, (unsigned char)(0xE0 | (want >> 12)));
        ps_grow_byte(g, (unsigned char)(0x80 | ((want >> 6) & 0x3F)));
        ps_grow_byte(g, (unsigned char)(0x80 | (want & 0x3F)));
    } else {
        ps_grow_byte(g, (unsigned char)(0xF0 | (want >> 18)));
        ps_grow_byte(g, (unsigned char)(0x80 | ((want >> 12) & 0x3F)));
        ps_grow_byte(g, (unsigned char)(0x80 | ((want >> 6) & 0x3F)));
        ps_grow_byte(g, (unsigned char)(0x80 | (want & 0x3F)));
    }
}

/* True when the code point's title mapping is the identity: the titlecase
   letters swapcase to themselves (unlike the digraph upper letters, whose
   title mapping moves). */
static int ps_case_is_title_identity(uint32_t cp) {
    uint32_t tlen = 0;
    const char *ts = ps_case_exp(ps_case_exp_title, PS_CASE_EXP_TITLE_N, cp, &tlen);
    if (ts == NULL) {
        /* Not in the specials table: the title mapping is the upper
           mapping, which differs for every char with both directions
           differing - the only callers of this predicate. */
        return 0;
    }
    char buf[4];
    int w = 0;
    if (cp < 0x80) {
        buf[w++] = (char)cp;
    } else if (cp < 0x800) {
        buf[w++] = (char)(0xC0 | (cp >> 6));
        buf[w++] = (char)(0x80 | (cp & 0x3F));
    } else if (cp < 0x10000) {
        buf[w++] = (char)(0xE0 | (cp >> 12));
        buf[w++] = (char)(0x80 | ((cp >> 6) & 0x3F));
        buf[w++] = (char)(0x80 | (cp & 0x3F));
    } else {
        buf[w++] = (char)(0xF0 | (cp >> 18));
        buf[w++] = (char)(0x80 | ((cp >> 12) & 0x3F));
        buf[w++] = (char)(0x80 | ((cp >> 6) & 0x3F));
        buf[w++] = (char)(0x80 | (cp & 0x3F));
    }
    return tlen == (uint32_t)w && memcmp(ts, buf, (size_t)w) == 0;
}

/* The lower mapping of one code point inside a sequence: the final-sigma
   rule maps U+03A3 to U+03C2 in final position. `cps` may be NULL when the
   caller only has the single code point (then the context is empty). */
static uint32_t ps_case_lower_cp(const uint32_t *cps, int64_t n, int64_t at, uint32_t cp) {
    if (cp == 0x3A3 && cps != NULL && ps_sigma_final(cps, n, at)) {
        return 0x3C2;
    }
    const PsCaseRec *rec = ps_case_find(cp);
    return rec != NULL ? rec->lo : cp;
}

/* The replacement for one code point under `kind`: appended to `g`.
   The title kind uses the title mapping (the specials table, then upper)
   at a word start and the lower mapping otherwise; lower expands the
   final sigma. Returns nothing; *prev_cased receives the Cased property
   of the input code point (the title word scan feeds it back). */
static void ps_case_apply(PsGrow *g, uint32_t cp, PsCaseKind kind, int title_first, const uint32_t *cps, int64_t n, int64_t at, int *prev_cased) {
    const PsCaseRec *rec = ps_case_find(cp);
    uint32_t want;
    /* The kind whose expansion table answers a PS_CASE_EXP want: the
       non-start path of title lowers, so its expansions are lower ones. */
    PsCaseKind exp_kind = kind;
    if (kind == PS_CASE_TO_UPPER) {
        want = rec != NULL ? rec->up : cp;
    } else if (kind == PS_CASE_TO_LOWER) {
        want = ps_case_lower_cp(cps, n, at, cp);
    } else if (kind == PS_CASE_TO_FOLD) {
        want = rec != NULL ? rec->fold : cp;
    } else {
        /* Title: the specials table first (the titlecase letters and the
           eszett), then the upper mapping for a word start, the lower
           mapping otherwise. */
        uint32_t tlen = 0;
        const char *ts = title_first ? ps_case_exp(ps_case_exp_title, PS_CASE_EXP_TITLE_N, cp, &tlen) : NULL;
        if (ts != NULL) {
            ps_grow_add(g, ts, (int64_t)tlen);
            *prev_cased = ps_case_cased(cp);
            return;
        }
        if (title_first) {
            want = rec != NULL ? rec->up : cp;
        } else {
            want = ps_case_lower_cp(cps, n, at, cp);
            exp_kind = PS_CASE_TO_LOWER;
        }
    }
    if (want == PS_CASE_EXP) {
        const PsCaseExp *table = exp_kind == PS_CASE_TO_UPPER ? ps_case_exp_up
                                 : exp_kind == PS_CASE_TO_LOWER ? ps_case_exp_lo
                                 : exp_kind == PS_CASE_TO_TITLE ? ps_case_exp_up
                                                              : ps_case_exp_fold;
        int64_t n_exp = exp_kind == PS_CASE_TO_UPPER ? PS_CASE_EXP_UP_N
                        : exp_kind == PS_CASE_TO_LOWER ? PS_CASE_EXP_LO_N
                        : exp_kind == PS_CASE_TO_TITLE ? PS_CASE_EXP_UP_N
                                                       : PS_CASE_EXP_FOLD_N;
        uint32_t len = 0;
        const char *s = ps_case_exp(table, n_exp, cp, &len);
        if (s != NULL && len > 0) {
            ps_grow_add(g, s, (int64_t)len);
            *prev_cased = ps_case_cased(cp);
            return;
        }
        /* The expansion table of this kind has no entry: fall through to
           the single-value path. */
    }
    /* A single-code-point mapping: emit the mapped value as UTF-8. The
       identity (want == cp) lands here too, which is the unmapped pass-
       through the case functions need. */
    ps_case_emit_cp(g, want);
    *prev_cased = ps_case_cased(cp);
}

/* Decode the whole byte string into a code-point array (allocation via
   malloc; the case functions abort on OOM like the rest of the runtime). */
static uint32_t *ps_casecps(int64_t n, const char *p, int64_t *out_n) {
    int64_t cap = n + 1;
    uint32_t *cps = malloc((size_t)cap * sizeof(uint32_t));
    if (cps == NULL) {
        abort();
    }
    int64_t at = 0;
    int64_t m = 0;
    while (at < n) {
        uint32_t cp;
        int w;
        ps_case_decode(n, p, at, &cp, &w);
        cps[m++] = cp;
        at += w;
    }
    *out_n = m;
    return cps;
}

static void ps_case_run(int64_t n, const char *p, PsCaseKind kind, PsBuf *out) {
    uint32_t *cps = NULL;
    int64_t m = 0;
    if (n > 0) {
        cps = ps_casecps(n, p, &m);
    }
    PsGrow g;
    ps_grow_init(&g);
    int word_start = 1;
    for (int64_t i = 0; i < m; i++) {
        int prev_cased = 0;
        ps_case_apply(&g, cps[i], kind, kind == PS_CASE_TO_TITLE && word_start, cps, m, i, &prev_cased);
        word_start = !prev_cased;
    }
    free(cps);
    ps_grow_finish(&g, out);
}

void ps_str_upper(int64_t n, char *p, PsBuf *out) { ps_case_run(n, p, PS_CASE_TO_UPPER, out); }
void ps_str_lower(int64_t n, char *p, PsBuf *out) { ps_case_run(n, p, PS_CASE_TO_LOWER, out); }
void ps_str_title(int64_t n, const char *p, PsBuf *out) { ps_case_run(n, p, PS_CASE_TO_TITLE, out); }
void ps_str_capitalize(int64_t n, const char *p, PsBuf *out) {
    /* CPython's capitalize: the first code point takes the title mapping,
       every following one the context-aware lower (the final-sigma rule
       sees the whole string). */
    uint32_t *cps = NULL;
    int64_t m = 0;
    if (n > 0) {
        cps = ps_casecps(n, p, &m);
    }
    PsGrow g;
    ps_grow_init(&g);
    for (int64_t i = 0; i < m; i++) {
        int prev_cased = 0;
        ps_case_apply(&g, cps[i], PS_CASE_TO_TITLE, i == 0, cps, m, i, &prev_cased);
    }
    free(cps);
    ps_grow_finish(&g, out);
}
void ps_str_casefold(int64_t n, const char *p, PsBuf *out) { ps_case_run(n, p, PS_CASE_TO_FOLD, out); }
void ps_str_swapcase(int64_t n, const char *p, PsBuf *out) {
    /* CPython's swapcase: a lower code point takes the full upper mapping,
       an upper one the context-aware lower (the final-sigma rule applies),
       everything else is identity. The tables classify a code point by
       which mapping directions differ from identity: only up differs means
       a lower char, only lo differs an upper char, and both differing is
       an Lt (title == identity, swaps to itself) or a digraph Lu (title !=
       identity, swaps down). */
    uint32_t *cps = NULL;
    int64_t m = 0;
    if (n > 0) {
        cps = ps_casecps(n, p, &m);
    }
    PsGrow g;
    ps_grow_init(&g);
    for (int64_t i = 0; i < m; i++) {
        uint32_t cp = cps[i];
        const PsCaseRec *rec = ps_case_find(cp);
        int up_diff = rec != NULL && rec->up != cp;
        int lo_diff = rec != NULL && rec->lo != cp;
        int prev_cased = 0;
        if (up_diff && !lo_diff) {
            ps_case_apply(&g, cp, PS_CASE_TO_UPPER, 0, cps, m, i, &prev_cased);
        } else if (lo_diff && (!up_diff || !ps_case_is_title_identity(cp))) {
            ps_case_apply(&g, cp, PS_CASE_TO_LOWER, 0, cps, m, i, &prev_cased);
        } else {
            ps_case_emit_cp(&g, cp);
        }
    }
    free(cps);
    ps_grow_finish(&g, out);
}

void ps_str_repeat(int64_t n, char *p, int64_t count, PsBuf *out) {
    if (count < 0) {
        count = 0;
    }
    if (count != 0 && n > INT64_MAX / count) {
        ps_raise_code(PS_OVERFLOW);
    }
    int64_t total = n * count;
    char *q = malloc((size_t)total + 1);
    if (q == NULL) {
        abort();
    }
    for (int64_t i = 0; i < count; i++) {
        memcpy(q + i * n, p, (size_t)n);
    }
    q[total] = '\0';
    out->len = total;
    out->data = q;
}

void ps_str_replace(int64_t n, char *p, int64_t on, char *op, int64_t nn, char *np, PsBuf *out) {
    if (on == 0) {
        if (n > INT64_MAX - 1 || (nn > 0 && (n + 1) > INT64_MAX / nn)) {
            ps_raise_code(PS_OVERFLOW);
        }
        int64_t total = (n + 1) * nn + n;
        char *q = malloc((size_t)total + 1);
        if (q == NULL) {
            abort();
        }
        char *w = q;
        for (int64_t i = 0; i < n; i++) {
            if (nn > 0) {
                memcpy(w, np, (size_t)nn);
                w += nn;
            }
            *w++ = p[i];
        }
        if (nn > 0) {
            memcpy(w, np, (size_t)nn);
            w += nn;
        }
        *w = '\0';
        out->len = total;
        out->data = q;
        return;
    }
    int64_t count = 0;
    for (int64_t i = 0; i + on <= n;) {
        if (memcmp(p + i, op, (size_t)on) == 0) {
            count++;
            i += on;
        } else {
            i++;
        }
    }
    int64_t total = n + count * (nn - on);
    if (total < 0) {
        ps_raise_code(PS_OVERFLOW);
    }
    char *q = malloc((size_t)total + 1);
    if (q == NULL) {
        abort();
    }
    char *w = q;
    for (int64_t i = 0; i < n;) {
        if (i + on <= n && memcmp(p + i, op, (size_t)on) == 0) {
            if (nn > 0) {
                memcpy(w, np, (size_t)nn);
                w += nn;
            }
            i += on;
        } else {
            *w++ = p[i++];
        }
    }
    *w = '\0';
    out->len = total;
    out->data = q;
}

static int64_t ps_double_to_i64(double y) {
    if (isnan(y)) {
        ps_raise_cstr(PS_VALUE, "cannot convert float NaN to integer");
    }
    if (isinf(y) || y >= 9223372036854775808.0 || y < -9223372036854775808.0) {
        ps_raise_cstr(PS_OVERFLOW, "cannot convert float to i64");
    }
    return (int64_t)y;
}

int64_t ps_float_to_int(double x) { return ps_double_to_i64(trunc(x)); }

int64_t ps_math_floor(double x) { return ps_double_to_i64(floor(x)); }
int64_t ps_math_ceil(double x) { return ps_double_to_i64(ceil(x)); }
int64_t ps_math_trunc(double x) { return ps_double_to_i64(trunc(x)); }

double ps_math_sqrt(double x) {
    if (isnan(x)) {
        return x;
    }
    if (x < 0) {
        ps_raise_cstr(PS_VALUE, "math domain error");
    }
    return sqrt(x);
}

double ps_math_fabs(double x) { return fabs(x); }

static double ps_finite(double x, double (*fn)(double)) {
    if (isinf(x)) {
        ps_raise_cstr(PS_VALUE, "expected a finite input");
    }
    return fn(x);
}

double ps_math_sin(double x) { return ps_finite(x, sin); }
double ps_math_cos(double x) { return ps_finite(x, cos); }
double ps_math_tan(double x) { return ps_finite(x, tan); }

double ps_math_exp(double x) {
    if (isnan(x)) {
        return x;
    }
    double r = exp(x);
    if (isinf(r)) {
        ps_raise_cstr(PS_OVERFLOW, "math range error");
    }
    return r;
}

static double ps_positive(double x, double (*fn)(double)) {
    if (isnan(x)) {
        return x;
    }
    if (!(x > 0.0)) {
        ps_raise_cstr(PS_VALUE, "expected a positive input");
    }
    return fn(x);
}

double ps_math_log(double x) { return ps_positive(x, log); }
double ps_math_log2(double x) { return ps_positive(x, log2); }
double ps_math_log10(double x) { return ps_positive(x, log10); }

double ps_math_hypot(double x, double y) {
    double r = hypot(x, y);
    if (isfinite(x) && isfinite(y) && isinf(r)) {
        ps_raise_cstr(PS_OVERFLOW, "math range error");
    }
    return r;
}

double ps_math_atan2(double y, double x) { return atan2(y, x); }

int64_t ps_math_gcd(int64_t a, int64_t b) {
    uint64_t x = a < 0 ? 0 - (uint64_t)a : (uint64_t)a;
    uint64_t y = b < 0 ? 0 - (uint64_t)b : (uint64_t)b;
    while (y != 0) {
        uint64_t t = x % y;
        x = y;
        y = t;
    }
    if (x > (uint64_t)INT64_MAX) {
        ps_raise_cstr(PS_OVERFLOW, "Python int too large to convert to C long");
    }
    return (int64_t)x;
}

int32_t ps_math_isclose(double a, double b, double rel_tol, double abs_tol) {
    if (rel_tol < 0.0 || abs_tol < 0.0) {
        ps_raise_cstr(PS_VALUE, "tolerances must be non-negative");
    }
    if (a == b) {
        return 1;
    }
    if (isinf(a) || isinf(b)) {
        return 0;
    }
    double diff = fabs(b - a);
    return (diff <= fabs(rel_tol * b) || diff <= fabs(rel_tol * a) || diff <= abs_tol) ? 1 : 0;
}

double ps_math_pow(double x, double y) {
    if (isnan(x)) {
        return pow(x, y);
    }
    if (x == 0.0 && y < 0.0 && !isnan(y)) {
        ps_raise_cstr(PS_VALUE, "math domain error");
    }
    if (x < 0.0 && !isnan(y)) {
        double t = trunc(y);
        if (t != y) {
            ps_raise_cstr(PS_VALUE, "math domain error");
        }
    }
    double r = pow(x, y);
    if (isfinite(x) && isfinite(y) && isinf(r)) {
        ps_raise_cstr(PS_OVERFLOW, "math range error");
    }
    return r;
}

static int64_t ps_slice_at(int64_t n, int64_t i, int64_t present, int64_t fallback) {
    if (!present) {
        return fallback;
    }
    if (i < 0) {
        if (n == 0 || i < -n) {
            return 0;
        }
        i += n;
    }
    if (i < 0) {
        return 0;
    }
    if (i > n) {
        return n;
    }
    return i;
}

static void ps_slice_bounds(
    int64_t n, int64_t start, int64_t stop, int64_t has_start, int64_t has_stop, int64_t *lo, int64_t *hi
) {
    int64_t left = ps_slice_at(n, start, has_start, 0);
    int64_t right = ps_slice_at(n, stop, has_stop, n);
    if (right < left) {
        right = left;
    }
    *lo = left;
    *hi = right;
}

static void ps_slice_step_ok(int64_t step) {
    if (step == 0) {
        ps_raise_cstr(PS_VALUE, "slice step cannot be zero");
    }
}

static int64_t ps_slice_count(int64_t lo, int64_t hi, int64_t step) {
    if (hi <= lo) {
        return 0;
    }
    int64_t span = hi - lo;
    int64_t count = span / step;
    if (span % step != 0) {
        count++;
    }
    return count;
}

/* Negative steps clamp like CPython: wrap negatives, then pin to [-1, n-1].
   Missing start defaults to n-1, missing stop to -1 (walk through index 0). */
static int64_t ps_slice_at_neg(int64_t n, int64_t i, int64_t present, int64_t fallback) {
    if (!present) {
        return fallback;
    }
    if (i < 0) {
        i += n;
    }
    if (i < 0) {
        return -1;
    }
    if (i > n - 1) {
        return n - 1;
    }
    return i;
}

static void ps_slice_bounds_neg(
    int64_t n, int64_t start, int64_t stop, int64_t has_start, int64_t has_stop, int64_t *lo, int64_t *hi
) {
    *lo = ps_slice_at_neg(n, start, has_start, n - 1);
    *hi = ps_slice_at_neg(n, stop, has_stop, -1);
}

static int64_t ps_slice_count_neg(int64_t from, int64_t to, int64_t step) {
    if (from <= to) {
        return 0;
    }
    return (from - to - 1) / (-step) + 1;
}

void ps_str_slice(
    int64_t n, const char *p, int64_t start, int64_t stop, int64_t has_start, int64_t has_stop, int64_t step, PsBuf *out
) {
    /* Slice bounds are code-point bounds. An ASCII string keeps the byte
       arithmetic (the flag makes one code point one byte); a non-ASCII
       string maps both ends through the O(n) scan once each. The step-1
       result is a view; a stepped slice copies the code points it picks. */
    ps_slice_step_ok(step);
    if (step > 0) {
        int64_t lo = 0;
        int64_t hi = 0;
        if (ps_all_ascii(n, p)) {
            ps_slice_bounds(n, start, stop, has_start, has_stop, &lo, &hi);
            if (step == 1) {
                ps_view(out, p + lo, hi - lo);
                return;
            }
            int64_t count = ps_slice_count(lo, hi, step);
            if (count == 0) {
                ps_view(out, "", 0);
                return;
            }
            char *q = malloc((size_t)count + 1);
            if (q == NULL) {
                abort();
            }
            int64_t i = lo;
            for (int64_t w = 0; w < count; w++) {
                q[w] = p[i];
                i += step;
            }
            q[count] = '\0';
            out->len = count;
            out->data = q;
            return;
        }
        int64_t chars = ps_str_nchars(n, p);
        ps_slice_bounds(chars, start, stop, has_start, has_stop, &lo, &hi);
        int64_t at = ps_str_offset(n, p, lo);
        int64_t end = hi >= chars ? n : ps_str_offset(n, p, hi);
        if (step == 1) {
            ps_view(out, p + at, end - at);
            return;
        }
        int64_t count = ps_slice_count(lo, hi, step);
        if (count == 0) {
            ps_view(out, "", 0);
            return;
        }
        PsGrow grow;
        ps_grow_init(&grow);
        int64_t i = lo;
        for (int64_t w = 0; w < count; w++) {
            int64_t off = ps_str_offset(n, p, i);
            ps_grow_add(&grow, p + off, ps_str_char_len(n, p, off));
            i += step;
        }
        ps_grow_finish(&grow, out);
        return;
    }
    /* Negative step: code-point indices, walked right to left. */
    int64_t chars = ps_str_nchars(n, p);
    int64_t lo = 0;
    int64_t hi = 0;
    ps_slice_bounds_neg(chars, start, stop, has_start, has_stop, &lo, &hi);
    int64_t count = ps_slice_count_neg(lo, hi, step);
    if (count == 0) {
        ps_view(out, "", 0);
        return;
    }
    PsGrow grow;
    ps_grow_init(&grow);
    int64_t i = lo;
    for (int64_t w = 0; w < count; w++) {
        int64_t off = ps_str_offset(n, p, i);
        ps_grow_add(&grow, p + off, ps_str_char_len(n, p, off));
        i += step;
    }
    ps_grow_finish(&grow, out);
}

PsSeq *ps_seq_slice(PsSeq *s, int64_t start, int64_t stop, int64_t has_start, int64_t has_stop, int64_t step) {
    ps_slice_step_ok(step);
    int64_t lo = 0;
    int64_t hi = 0;
    if (step < 0) {
        ps_slice_bounds_neg(s->len, start, stop, has_start, has_stop, &lo, &hi);
    } else {
        ps_slice_bounds(s->len, start, stop, has_start, has_stop, &lo, &hi);
    }
    if (step == 1) {
        int64_t count = hi - lo;
        PsSeq *out = ps_seq_new(s->elem, count);
        if (count > 0 && s->data != NULL) {
            memcpy(out->data, (char *)s->data + (size_t)lo * (size_t)s->elem, (size_t)count * (size_t)s->elem);
        }
        return out;
    }
    int64_t count = step < 0 ? ps_slice_count_neg(lo, hi, step) : ps_slice_count(lo, hi, step);
    PsSeq *out = ps_seq_new(s->elem, count);
    if (count > 0 && s->data != NULL) {
        int64_t i = lo;
        for (int64_t w = 0; w < count; w++) {
            memcpy(
                (char *)out->data + (size_t)w * (size_t)s->elem,
                (char *)s->data + (size_t)i * (size_t)s->elem,
                (size_t)s->elem
            );
            i += step;
        }
    }
    return out;
}

/* `xs[a:b] = items` with the step-1 bounds of ps_seq_slice. Grows through the
same capacity policy as ps_seq_push, then shifts the tail once. */
void ps_seq_splice(PsSeq *s, int64_t start, int64_t stop, int64_t has_start, int64_t has_stop, PsSeq *items) {
    int64_t lo = 0;
    int64_t hi = 0;
    ps_slice_bounds(s->len, start, stop, has_start, has_stop, &lo, &hi);
    if (items->elem != s->elem) {
        ps_raise_code(PS_VALUE);
    }
    int64_t drop = hi - lo;
    int64_t add = items->len;
    int64_t len = s->len - drop + add;
    if (len > s->cap) {
        int64_t cap = s->cap == 0 ? 4 : s->cap;
        while (cap < len) {
            cap *= 2;
        }
        if ((uint64_t)cap > UINT64_MAX / (uint64_t)s->elem) {
            ps_raise_code(PS_OVERFLOW);
        }
        void *data = realloc(s->data, (size_t)cap * (size_t)s->elem);
        if (data == NULL) {
            abort();
        }
        s->data = data;
        s->cap = cap;
    }
    /* `xs[a:b] = xs` reads its own cells; snapshot them before the shift. */
    void *src = items->data;
    void *held = NULL;
    if (items == s && add > 0) {
        held = malloc((size_t)add * (size_t)s->elem);
        if (held == NULL) {
            abort();
        }
        memcpy(held, items->data, (size_t)add * (size_t)s->elem);
        src = held;
    }
    memmove(
        (char *)s->data + (size_t)(lo + add) * (size_t)s->elem,
        (char *)s->data + (size_t)hi * (size_t)s->elem,
        (size_t)(s->len - hi) * (size_t)s->elem
    );
    if (add > 0) {
        memcpy((char *)s->data + (size_t)lo * (size_t)s->elem, src, (size_t)add * (size_t)s->elem);
    }
    s->len = len;
    free(held);
    ps_seq_index_invalidate(s);
}

static int ps_ascii_space(unsigned char c) {
    return c == ' ' || c == '\t' || c == '\n' || c == '\r' || c == '\v' || c == '\f';
}

static int ps_text_space(unsigned char c) { return ps_space(c) || c == 0x85 || c == 0xa0; }

static void ps_push_view(PsSeq *seq, const char *p, int64_t n) {
    PsBuf piece;
    ps_view(&piece, p, n);
    ps_seq_push(seq, &piece);
}

PsSeq *ps_str_split(int64_t n, const char *p, int64_t sn, const char *sp, int64_t mode) {
    PsSeq *seq = ps_seq_new(sizeof(PsBuf), 0);
    if (mode == 0 && sn == 0) {
        ps_raise_cstr(PS_VALUE, "empty separator");
    }
    if (mode != 0) {
        int64_t i = 0;
        while (i < n) {
            unsigned char c = (unsigned char)p[i];
            int space = mode == 2 ? ps_ascii_space(c) : ps_text_space(c);
            if (space) {
                i++;
                continue;
            }
            int64_t j = i + 1;
            while (j < n) {
                unsigned char d = (unsigned char)p[j];
                int gap = mode == 2 ? ps_ascii_space(d) : ps_text_space(d);
                if (gap) {
                    break;
                }
                j++;
            }
            ps_push_view(seq, p + i, j - i);
            i = j;
        }
        return seq;
    }
    int64_t i = 0;
    while (i <= n) {
        int64_t j = i;
        int found = 0;
        while (j + sn <= n) {
            if (memcmp(p + j, sp, (size_t)sn) == 0) {
                found = 1;
                break;
            }
            j++;
        }
        if (!found) {
            ps_push_view(seq, p + i, n - i);
            break;
        }
        ps_push_view(seq, p + i, j - i);
        i = j + sn;
        if (i == n) {
            ps_push_view(seq, p + n, 0);
            break;
        }
    }
    return seq;
}

void ps_str_join(int64_t n, const char *sep, PsSeq *parts, PsBuf *out) {
    if (parts->elem != (int64_t)sizeof(PsBuf)) {
        ps_raise_cstr(PS_VALUE, "join takes a list of str or bytes");
    }
    if (parts->len == 0) {
        ps_buf_copy(out, "", 0);
        return;
    }
    int64_t total = 0;
    for (int64_t i = 0; i < parts->len; i++) {
        PsBuf piece;
        memcpy(&piece, (char *)parts->data + (size_t)i * sizeof(PsBuf), sizeof(PsBuf));
        if (i > 0) {
            if (n < 0 || total > INT64_MAX - n) {
                ps_raise_code(PS_OVERFLOW);
            }
            total += n;
        }
        if (piece.len < 0 || total > INT64_MAX - piece.len) {
            ps_raise_code(PS_OVERFLOW);
        }
        total += piece.len;
    }
    char *q = malloc((size_t)total + 1);
    if (q == NULL) {
        abort();
    }
    char *w = q;
    for (int64_t i = 0; i < parts->len; i++) {
        PsBuf piece;
        memcpy(&piece, (char *)parts->data + (size_t)i * sizeof(PsBuf), sizeof(PsBuf));
        if (i > 0 && n > 0) {
            memcpy(w, sep, (size_t)n);
            w += n;
        }
        if (piece.len > 0) {
            memcpy(w, piece.data, (size_t)piece.len);
            w += piece.len;
        }
    }
    *w = '\0';
    out->len = total;
    out->data = q;
}

void ps_utf8_encode(int64_t n, const char *p, PsBuf *out) {
    /* encode("utf-8"): the buffer holds code points as UTF-8 already, so
       this is a copy. The name is kept for the emitter's ABI stability; the
       Latin-1 lifting the old two-byte body did is gone with the model. */
    ps_buf_copy(out, p, n);
}

void ps_utf8_decode(int64_t n, const char *p, PsBuf *out) {
    /* decode("utf-8") over raw bytes: one to four byte sequences, with
       CPython's rejections (surrogates, overlongs, above U+10FFFF,
       continuation or truncated tails) as ValueError-family raises. The
       valid result is the bytes themselves, so validation is the work. */
    int64_t i = 0;
    while (i < n) {
        unsigned char c = (unsigned char)p[i];
        if (c < 0x80) {
            i++;
            continue;
        }
        unsigned int cp;
        int w;
        if ((c & 0xE0) == 0xC0) {
            w = 2;
            cp = (unsigned int)(c & 0x1F);
        } else if ((c & 0xF0) == 0xE0) {
            w = 3;
            cp = (unsigned int)(c & 0x0F);
        } else if ((c & 0xF8) == 0xF0) {
            w = 4;
            cp = (unsigned int)(c & 0x07);
        } else {
            ps_raise_cstr(PS_UNICODEDECODE, "'utf-8' codec can't decode byte in position: invalid continuation byte");
        }
        if (i + w > n) {
            ps_raise_cstr(PS_UNICODEDECODE, "'utf-8' codec can't decode byte in position: unexpected end of data");
        }
        for (int k = 1; k < w; k++) {
            unsigned char cc = (unsigned char)p[i + k];
            if ((cc & 0xC0) != 0x80) {
                ps_raise_cstr(PS_UNICODEDECODE, "'utf-8' codec can't decode byte in position: invalid continuation byte");
            }
            cp = (cp << 6) | (unsigned int)(cc & 0x3F);
        }
        if (w == 2 && cp < 0x80) {
            ps_raise_cstr(PS_UNICODEDECODE, "'utf-8' codec can't decode byte in position: invalid start byte");
        }
        if (w == 3 && cp < 0x800) {
            ps_raise_cstr(PS_UNICODEDECODE, "'utf-8' codec can't decode byte in position: invalid start byte");
        }
        if (w == 4 && cp < 0x10000) {
            ps_raise_cstr(PS_UNICODEDECODE, "'utf-8' codec can't decode byte in position: invalid start byte");
        }
        if (cp > 0x10FFFF || (cp >= 0xD800 && cp <= 0xDFFF)) {
            ps_raise_cstr(PS_UNICODEDECODE, "'utf-8' codec can't decode byte in position: code point out of range");
        }
        i += w;
    }
    ps_buf_copy(out, p, n);
}

void ps_latin1_encode(int64_t n, const char *p, PsBuf *out) {
    /* encode("latin-1"): a raise above U+00FF is the codec's boundary
       behavior, not a fence. The buffer holds UTF-8, so the walk re-decodes
       it code point by code point; the ASCII fast path copies. */
    int64_t at = 0;
    while (at < n) {
        unsigned char c = (unsigned char)p[at];
        if (c < 0x80) {
            at++;
            continue;
        }
        int w = ps_utf8_len(c);
        unsigned int cp = 0;
        int64_t end = at + w <= n ? at + w : n;
        for (int k = 1; k < w && at + k < end; k++) {
            cp = (cp << 6) | (unsigned int)((unsigned char)p[at + k] & 0x3F);
        }
        cp |= (unsigned int)(c & (w == 2 ? 0x1F : w == 3 ? 0x0F : 0x07)) << ((w - 1) * 6);
        if (cp > 0xFF) {
            ps_raise_cstr(PS_UNICODEENCODE, "'latin-1' codec can't encode character in position: ordinal not in range(256)");
        }
        at += w;
    }
    ps_buf_copy(out, p, n);
}

void ps_latin1_decode(int64_t n, const char *p, PsBuf *out) {
    /* decode("latin-1"): every byte becomes the code point of the same
       value, so the UTF-8 encoding of the result is the bytes lifted
       through the two/three-byte forms. */
    PsGrow grow;
    ps_grow_init(&grow);
    for (int64_t i = 0; i < n; i++) {
        unsigned char c = (unsigned char)p[i];
        if (c < 0x80) {
            ps_grow_byte(&grow, c);
        } else {
            ps_grow_byte(&grow, (unsigned char)(0xC0 | (c >> 6)));
            ps_grow_byte(&grow, (unsigned char)(0x80 | (c & 0x3F)));
        }
    }
    ps_grow_finish(&grow, out);
}

void ps_i64_str(int64_t v, PsBuf *out) {
    char tmp[32];
    int wrote = snprintf(tmp, sizeof tmp, "%lld", (long long)v);
    if (wrote < 0) {
        ps_raise_cstr(PS_VALUE, "int to str failed");
    }
    ps_buf_copy(out, tmp, wrote);
}

static void ps_normalize_exp(char *tmp) {
    char *exp = strchr(tmp, 'e');
    if (exp == NULL) {
        exp = strchr(tmp, 'E');
    }
    if (exp == NULL) {
        return;
    }
    *exp = 'e';
    char *digits = exp + 1;
    int sign = 1;
    if (*digits == '+') {
        digits++;
    } else if (*digits == '-') {
        sign = -1;
        digits++;
    }
    int value = 0;
    while (*digits >= '0' && *digits <= '9') {
        value = value * 10 + (*digits - '0');
        digits++;
    }
    snprintf(exp, 8, "e%c%02d", sign < 0 ? '-' : '+', value);
}

void ps_f64_str(double v, PsBuf *out) {
    char tmp[128];
    if (isnan(v)) {
        ps_buf_copy(out, "nan", 3);
        return;
    }
    if (isinf(v)) {
        ps_buf_copy(out, signbit(v) ? "-inf" : "inf", signbit(v) ? 4 : 3);
        return;
    }
    if (v == 0.0) {
        ps_buf_copy(out, signbit(v) ? "-0.0" : "0.0", signbit(v) ? 4 : 3);
        return;
    }
    int prec = 1;
    for (; prec <= 17; prec++) {
        snprintf(tmp, sizeof tmp, "%.*g", prec, v);
        char *end = NULL;
        double back = strtod(tmp, &end);
        if (end != tmp && end != NULL && *end == '\0' && back == v) {
            break;
        }
    }
    int has_dot = 0;
    int has_exp = 0;
    for (char *cursor = tmp; *cursor != '\0'; cursor++) {
        if (*cursor == '.') {
            has_dot = 1;
        }
        if (*cursor == 'e' || *cursor == 'E') {
            has_exp = 1;
        }
    }
    if (!has_dot && !has_exp) {
        size_t len = strlen(tmp);
        if (len + 3 < sizeof tmp) {
            tmp[len] = '.';
            tmp[len + 1] = '0';
            tmp[len + 2] = '\0';
        }
    }
    if (has_exp) {
        ps_normalize_exp(tmp);
    }
    ps_buf_copy(out, tmp, (int64_t)strlen(tmp));
}

static int64_t ps_seq_i64(PsSeq *s, int64_t index) {
    int64_t value = 0;
    memcpy(&value, (char *)s->data + (size_t)index * (size_t)s->elem, sizeof(int64_t));
    return value;
}

int64_t ps_bisect_left(PsSeq *s, int64_t key) {
    if (s->elem != (int64_t)sizeof(int64_t)) {
        ps_raise_cstr(PS_VALUE, "bisect takes a list of int");
    }
    int64_t lo = 0;
    int64_t hi = s->len;
    while (lo < hi) {
        int64_t mid = lo + (hi - lo) / 2;
        if (ps_seq_i64(s, mid) < key) {
            lo = mid + 1;
        } else {
            hi = mid;
        }
    }
    return lo;
}

int64_t ps_bisect_right(PsSeq *s, int64_t key) {
    if (s->elem != (int64_t)sizeof(int64_t)) {
        ps_raise_cstr(PS_VALUE, "bisect takes a list of int");
    }
    int64_t lo = 0;
    int64_t hi = s->len;
    while (lo < hi) {
        int64_t mid = lo + (hi - lo) / 2;
        if (ps_seq_i64(s, mid) <= key) {
            lo = mid + 1;
        } else {
            hi = mid;
        }
    }
    return lo;
}

/* `insort_left(a, x)` inserts x before the leftmost equal cell, so it
   lands at the position `bisect_left` returns. `insort_right(a, x)`
   inserts it after the rightmost equal cell, at the position
   `bisect_right` returns. CPython's `insort` is the alias of
   `insort_right`. The list is mutated in place, like CPython's
   `a.insert(lo, x)`. */
void ps_bisect_insort_left(PsSeq *s, int64_t key) {
    int64_t at = ps_bisect_left(s, key);
    ps_seq_insert(s, at, &key);
}

void ps_bisect_insort_right(PsSeq *s, int64_t key) {
    int64_t at = ps_bisect_right(s, key);
    ps_seq_insert(s, at, &key);
}
