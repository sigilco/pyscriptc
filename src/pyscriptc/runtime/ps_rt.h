/* Shared layout for the runtime linked beside emitted LLVM.

%ps.str is { i64, ptr }. PsBuf must stay 16 bytes. PsSeq keeps len, elem, and
data in that order; cap is appended so existing stores still line up.
*/

#ifndef PYSCRIPTC_PS_RT_H
#define PYSCRIPTC_PS_RT_H

#include <stdint.h>
#include "ps_compat.h"

/* Builtin exception codes. The full hierarchy lives here (E2 M2): a parent
   always has a smaller code than its children, so ps_exc_isa walks the parent
   table towards zero. User classes register at PS_USER_BASE and up through
   ps_exc_register. The short legacy names below the table are the codes the
   runtime C files already raise under; they are aliases, not extra codes. */
enum {
    PS_BASEEXCEPTION = 1,
    PS_BASEEXCEPTIONGROUP = 2,
    PS_EXCEPTIONGROUP = 3,
    PS_GENERATOREXIT = 4,
    PS_KEYBOARDINTERRUPT = 5,
    PS_SYSTEMEXIT = 6,
    PS_EXCEPTION = 7,
    PS_ARITHMETICERROR = 8,
    PS_FLOATINGPOINTERROR = 9,
    PS_OVERFLOWERROR = 10,
    PS_ZERODIVISIONERROR = 11,
    PS_ASSERTIONERROR = 12,
    PS_ATTRIBUTEERROR = 13,
    PS_BUFFERERROR = 14,
    PS_EOFERROR = 15,
    PS_IMPORTERROR = 16,
    PS_MODULENOTFOUNDERROR = 17,
    PS_LOOKUPERROR = 18,
    PS_INDEXERROR = 19,
    PS_KEYERROR = 20,
    PS_MEMORYERROR = 21,
    PS_NAMEERROR = 22,
    PS_UNBOUNDLOCALERROR = 23,
    PS_OSERROR = 24,
    PS_BLOCKINGIOERROR = 25,
    PS_CHILDPROCESSERROR = 26,
    PS_CONNECTIONERROR = 27,
    PS_BROKENPIPEERROR = 28,
    PS_CONNECTIONABORTEDERROR = 29,
    PS_CONNECTIONREFUSEDERROR = 30,
    PS_CONNECTIONRESETERROR = 31,
    PS_FILEEXISTSERROR = 32,
    PS_FILENOTFOUNDERROR = 33,
    PS_INTERRUPTEDERROR = 34,
    PS_ISADIRECTORYERROR = 35,
    PS_NOTADIRECTORYERROR = 36,
    PS_PERMISSIONERROR = 37,
    PS_PROCESSLOOKUPERROR = 38,
    PS_TIMEOUTERROR = 39,
    PS_REFERENCEERROR = 40,
    PS_RUNTIMEERROR = 41,
    PS_NOTIMPLEMENTEDERROR = 42,
    PS_RECURSIONERROR = 43,
    PS_STOPASYNCITERATION = 44,
    PS_STOPITERATION = 45,
    PS_SYNTAXERROR = 46,
    PS_INDENTATIONERROR = 47,
    PS_TABERROR = 48,
    PS_SYSTEMERROR = 49,
    PS_TYPEERROR = 50,
    PS_VALUEERROR = 51,
    PS_UNICODEERROR = 52,
    PS_UNICODEDECODEERROR = 53,
    PS_UNICODEENCODEERROR = 54,
    PS_UNICODETRANSLATEERROR = 55,
    PS_WARNING = 56,
    PS_BYTESWARNING = 57,
    PS_DEPRECATIONWARNING = 58,
    PS_ENCODINGWARNING = 59,
    PS_FUTUREWARNING = 60,
    PS_IMPORTWARNING = 61,
    PS_PENDINGDEPRECATIONWARNING = 62,
    PS_RESOURCEWARNING = 63,
    PS_RUNTIMEWARNING = 64,
    PS_SYNTAXWARNING = 65,
    PS_UNICODEWARNING = 66,
    PS_USERWARNING = 67,
    PS_USER_BASE = 256
};

/* Legacy short names kept for the existing raise sites in the runtime files.
   Aliases only: they must keep resolving to the same class as the full name. */
enum {
    PS_VALUE = PS_VALUEERROR,
    PS_INDEX = PS_INDEXERROR,
    PS_KEY = PS_KEYERROR,
    PS_OVERFLOW = PS_OVERFLOWERROR,
    PS_ZERO_DIV = PS_ZERODIVISIONERROR,
    PS_RUNTIME = PS_RUNTIMEERROR,
    PS_STOP = PS_STOPITERATION,
    PS_FILE_NOT_FOUND = PS_FILENOTFOUNDERROR,
    PS_TYPE = PS_TYPEERROR,
    PS_ATTR = PS_ATTRIBUTEERROR
};

/* Reserved codes for the coroutine exceptions and the queue exceptions.
They sit at the top of the i32 code space so PS_USER_BASE (and every user
exception number) stays put. User class codes count up from PS_USER_BASE and
can never reach this range. Mirrored in builtins.RESERVED_EXCEPTIONS. */
enum {
    PS_RESERVED_BASE = 0x7FFFFFF0,
    PS_GENERATOR_EXIT = 0x7FFFFFF0,
    PS_STOP_ASYNC = 0x7FFFFFF1,
    PS_CANCELLED = 0x7FFFFFF2,
    PS_TIMEOUT = 0x7FFFFFF3,
    PS_QUEUE_EMPTY = 0x7FFFFFF4,
    PS_QUEUE_FULL = 0x7FFFFFF5
};

/* longjmp payload once an exception is pending; the class code lives on the
PsExc record, not on the wire. */
enum { PS_RAISED = -1 };

enum {
    PS_JSON_INT = 1,
    PS_JSON_BOOL = 2,
    PS_JSON_STR = 3,
    PS_JSON_LIST = 4,
    PS_JSON_DICT = 5,
    PS_JSON_FLOAT = 6,
    PS_JSON_OPT = 7
};

typedef struct PsBuf {
    int64_t len;
    char *data;
} PsBuf;

typedef struct PsSeq {
    int64_t len;
    int64_t elem;
    void *data;
    int64_t cap;
} PsSeq;

typedef struct PsMap {
    int64_t len; /* live keys */
    int64_t cap; /* dense rows the keys and vals arrays hold */
    int32_t key_kind;
    int64_t key_size;
    int64_t val_size;
    void *keys;
    void *vals;
    /* The open-addressing index (#382). The dense arrays above stay the
       source of truth for the iteration order (divergence 6: dict and set
       iteration is insertion order), and a row is never moved: a delete
       leaves the row in place and marks it dead, so the survivors keep
       their order and the delete stays O(1) amortized.

       slots is the probe table: a slot holds the dense row plus one, zero
       is an empty slot and -1 is a tombstone left by a delete. The table is
       a power of two with two slots per dense row, so it is never more than
       half full and a probe always ends on an empty slot.

       hashes caches the hash of every dense row (PS_DEAD_HASH marks a dead
       row), so a rebuild never re-hashes a string and a lookup compares the
       cached hash before the key bytes.

       order holds the live rows in insertion order. A delete leaves a hole
       in it, so it is rebuilt on demand; an insert appends to it. */
    int32_t *slots;
    int32_t slot_mask;
    int64_t slot_count;
    int64_t tombs; /* tombstones in the probe table */
    int64_t *hashes;
    int32_t *order; /* live rows in insertion order */
    int64_t order_len;
    int32_t order_dirty;
    int64_t rows; /* dense rows written so far; len is the live subset */
} PsMap;

typedef struct PsType {
    int32_t kind;
    int32_t child;
} PsType;

/* The open-file handle is private to ps_io.c. tempfile.NamedTemporaryFile
   lives in ps_osmore.c and wraps an mkstemp fd in one of these. */
typedef struct PsIO PsIO;
PsIO *ps_io_take_fd(int fd, const char *path, int64_t mode, int64_t keep);
PsIO *ps_temp_named(int64_t mode, int64_t keep);
void ps_io_read(PsIO *io, PsBuf *out);
int64_t ps_io_write(PsIO *io, int64_t n, const char *s);

/* ps_path.c: abspath normalization, shared with the tempfile tempdir pick. */
void ps_path_abs(int64_t n, const char *s, PsBuf *out);

typedef struct PsArg {
    int32_t kind;
    int32_t pad;
    int64_t i;
    double f;
    PsBuf b;
} PsArg;

typedef struct PsGrow {
    char *data;
    int64_t len;
    int64_t cap;
} PsGrow;

/* Raised exception. name and message are borrowed storage: name points at the
static class-name table, message at a 256-byte owned buffer like the old
ps_msg channel. cause links `raise ... from`; args is built lazily by
ps_exc_args from message (E3 stores real ctor args). */
typedef struct PsExc {
    int32_t code;
    int32_t flags;
    PsBuf name;
    PsBuf message;
    struct PsExc *cause;
    PsSeq *args;
} PsExc;

/* The message is already a repr (a KeyError's missing key, say), so the
report must not quote it a second time. */
#define PS_EXC_FLAG_PREQUOTED 1

extern char ps_empty_byte;

__attribute__((noreturn)) void ps_raise_cstr(int32_t code, const char *msg);
__attribute__((noreturn)) void ps_raise_code(int32_t code);
__attribute__((noreturn)) void ps_raise_zdi(int32_t variant);
__attribute__((noreturn)) void ps_reraise(void);
__attribute__((noreturn)) void ps_raise_exc(PsExc *e);
void ps_report_current(const char *prefix);
PsExc *ps_exc_current(void);
void ps_exc_restore(PsExc *e);
PsExc *ps_exc_make(int32_t code, int64_t n, const char *data);
/* Run `fn(env)` and catch the exception it raises: 0 returned (its status
   lands in `*status`), 1 raised (the record lands in `*out`). The event loop
   drives a child coroutine through this, so its failure travels to the
   awaiting parent instead of unwinding through the loop's C frame. */
int32_t ps_exc_catch(int32_t (*fn)(void *), void *env, int32_t *status, PsExc **out);
int32_t ps_exc_code_by_name(const char *name);
void ps_exc_set_cause(PsExc *e, PsExc *cause);
PsBuf ps_exc_str(PsExc *e);
void ps_exc_repr(PsExc *e, PsBuf *out);
void ps_exc_class(PsExc *e, PsBuf *out);
int32_t ps_exc_isa_value(PsExc *e, int32_t wanted);
PsSeq *ps_exc_args(PsExc *e);
int32_t ps_exc_code(void);
int32_t ps_exc_isa(int32_t raised, int32_t wanted);
void ps_exc_register(int32_t code, int32_t parent, const char *name);
void *ps_alloc(int64_t n);

PsSeq *ps_seq_new(int64_t elem, int64_t n);
int64_t ps_seq_len(PsSeq *s);
void ps_seq_get(PsSeq *s, int64_t i, void *out);
void ps_seq_set(PsSeq *s, int64_t i, void *src);
void ps_seq_push(PsSeq *s, const void *src);

PsMap *ps_map_new(int32_t key_kind, int64_t key_size, int64_t val_size, int64_t cap);
void ps_map_put(PsMap *m, void *key, void *val);
int32_t ps_map_has(PsMap *m, void *key);
void ps_map_get(PsMap *m, void *key, void *out);
int64_t ps_map_len(PsMap *m);
void ps_map_key_at(PsMap *m, int64_t i, void *out);
PsSeq *ps_dict_keys(PsMap *m);
void ps_map_val_at(PsMap *m, int64_t i, void *out);
/* The delete and the reset live beside the index they touch; ps_map_del and
   ps_map_clear in ps_more.c are the wrappers that raise on a missing key. */
int64_t ps_map_delete(PsMap *m, void *key);
void ps_map_reset(PsMap *m);

void ps_buf_view(PsBuf *out, const char *p, int64_t n);
void ps_buf_copy(PsBuf *out, const char *p, int64_t n);

void ps_grow_init(PsGrow *g);
void ps_grow_add(PsGrow *g, const char *p, int64_t n);
void ps_grow_byte(PsGrow *g, unsigned char c);
void ps_grow_finish(PsGrow *g, PsBuf *out);

void ps_str_slice(
    int64_t n, const char *p, int64_t start, int64_t stop, int64_t has_start, int64_t has_stop, int64_t step, PsBuf *out
);
PsSeq *ps_seq_slice(PsSeq *s, int64_t start, int64_t stop, int64_t has_start, int64_t has_stop, int64_t step);
PsSeq *ps_str_split(int64_t n, const char *p, int64_t sn, const char *sp, int64_t mode);
void ps_str_join(int64_t n, const char *sep, PsSeq *parts, PsBuf *out);
void ps_utf8_encode(int64_t n, const char *p, PsBuf *out);
void ps_utf8_decode(int64_t n, const char *p, PsBuf *out);
void ps_i64_str(int64_t v, PsBuf *out);
void ps_f64_str(double v, PsBuf *out);
int64_t ps_bisect_left(PsSeq *s, int64_t key);
int64_t ps_bisect_right(PsSeq *s, int64_t key);
int64_t ps_int_from_bytes(int64_t n, const char *s);

/* ps_more.c: R1-R5 helpers */
PsSeq *ps_seq_concat(PsSeq *a, PsSeq *b);
PsSeq *ps_seq_repeat(PsSeq *s, int64_t n);
void ps_seq_extend(PsSeq *dst, PsSeq *src);
void ps_seq_insert(PsSeq *s, int64_t i, void *src);
void ps_seq_reverse(PsSeq *s);
void ps_seq_sort_i64(PsSeq *s, int64_t desc);
void ps_seq_sort_f64(PsSeq *s, int64_t desc);
void ps_seq_sort_buf(PsSeq *s, int64_t desc);
int64_t ps_seq_index_i64(PsSeq *s, int64_t needle);
int64_t ps_seq_index_f64(PsSeq *s, double needle);
int64_t ps_seq_index_buf(PsSeq *s, int64_t n, char *p);
int64_t ps_seq_count_i64(PsSeq *s, int64_t needle);
int64_t ps_seq_count_f64(PsSeq *s, double needle);
int64_t ps_seq_count_buf(PsSeq *s, int64_t n, char *p);
void ps_seq_remove_i64(PsSeq *s, int64_t needle);
void ps_seq_remove_f64(PsSeq *s, double needle);
void ps_seq_remove_buf(PsSeq *s, int64_t n, char *p);
void ps_seq_insert_i64(PsSeq *s, int64_t i, int64_t v);
void ps_seq_insert_f64(PsSeq *s, int64_t i, double v);
void ps_seq_insert_buf(PsSeq *s, int64_t i, int64_t n, char *p);
int64_t ps_seq_pop_i64(PsSeq *s, int64_t i);
double ps_seq_pop_f64(PsSeq *s, int64_t i);
void ps_seq_pop_buf(PsSeq *s, int64_t i, PsBuf *out);
void ps_seq_del(PsSeq *s, int64_t i);
void ps_seq_splice(PsSeq *s, int64_t start, int64_t stop, int64_t has_start, int64_t has_stop, PsSeq *items);
int64_t ps_stdout_write(int64_t n, const char *s);
int64_t ps_stderr_write(int64_t n, const char *s);
void *ps_stderr_stream(void);
void ps_input_line(PsBuf *out);
void ps_map_del(PsMap *m, void *key);
void ps_map_clear(PsMap *m);
void ps_map_update(PsMap *dst, PsMap *src);
PsSeq *ps_dict_values(PsMap *m);
PsSeq *ps_dict_items(PsMap *m, int64_t voff, int64_t esz);
PsSeq *ps_str_split_m(
    int64_t n, const char *p, int64_t sn, const char *sp, int64_t mode, int64_t maxsplit, int64_t right
);
PsSeq *ps_str_splitlines(int64_t n, const char *p, int64_t keepends);
int64_t ps_str_count(int64_t n, const char *p, int64_t sn, const char *sp);
int64_t ps_str_index(int64_t n, const char *p, int64_t sn, const char *sp);
int64_t ps_str_rfind(int64_t n, const char *p, int64_t sn, const char *sp);
int64_t ps_str_isdigit(int64_t n, const char *p);
int64_t ps_str_isalpha(int64_t n, const char *p);
int64_t ps_str_isalnum(int64_t n, const char *p);
int64_t ps_str_isspace(int64_t n, const char *p);
int64_t ps_str_isupper(int64_t n, const char *p);
int64_t ps_str_islower(int64_t n, const char *p);
void ps_str_title(int64_t n, const char *p, PsBuf *out);
void ps_str_capitalize(int64_t n, const char *p, PsBuf *out);
void ps_str_casefold(int64_t n, const char *p, PsBuf *out);
void ps_str_swapcase(int64_t n, const char *p, PsBuf *out);
void ps_str_zfill(int64_t n, const char *p, int64_t width, PsBuf *out);
void ps_str_justify(
    int64_t n, const char *p, int64_t width, int64_t fn, const char *fc, int64_t side, PsBuf *out
);
void ps_str_removeprefix(int64_t n, const char *p, int64_t sn, const char *sp, PsBuf *out);
void ps_str_removesuffix(int64_t n, const char *p, int64_t sn, const char *sp, PsBuf *out);
void ps_str_expandtabs(int64_t n, const char *p, int64_t tabsize, PsBuf *out);
void ps_str_partition(int64_t n, const char *p, int64_t sn, const char *sp, int64_t right, PsBuf *out);
void ps_repr_bytes(int64_t n, const char *p, PsBuf *out);
double ps_float_from_str(int64_t n, const char *p);

/* ps_loop.c: the run-to-completion loop behind asyncio.run/sleep/gather. */
void ps_loop_reset(void);
void ps_loop_ready(void *frame);
void ps_loop_run(void);
void ps_loop_run_root(void *frame);
void ps_loop_sleep(void *frame, double delay);
void ps_loop_wait(void *child, void *parent);
/* `await child`: drive the child once under the loop's catch. 0 pending,
   1 done, 2 failed (the child raised; ps_await_raise re-raises it in the
   awaiting parent, whose own try sees it). */
int64_t ps_await_poll(void *child, void *parent);
void ps_await_raise(void *child);

/* ps_loop.c: the task lifetime behind asyncio.create_task. A task wraps a
   coroutine frame and outlives a single step, so a handle to it stays valid
   across a suspension. The handle is the task pointer itself. */
void *ps_task_new(void *frame);
void *ps_task_current(void);
void ps_task_set_current(void *task);
int64_t ps_task_done(void *task);
int64_t ps_task_cancel(void *task);
int64_t ps_task_cancelled(void *task);
/* `await task`: park the frame until the task completes. 1 when the task is
   already done, so the caller does not park; 2 when the task failed, so the
   caller re-raises it with ps_task_failure. */
int64_t ps_task_await(void *task, void *frame);
/* Re-raise the failure a task handle holds, in the awaiting frame. */
void ps_task_failure(void *task);
/* Read the result the task's coroutine produced. The frame's field 2 is
   `.ps_result`, and `size` is the element size the caller's type carries. */
void ps_task_result(void *task, int64_t size, void *out);

/* wait_for (stage D2): run `task` under a deadline on behalf of `frame`. 1
   when the task is already done; otherwise the deadline row is armed, the
   frame parks, and the resume re-enters with `ps_task_wait_outcome`. */
int64_t ps_task_wait_for(void *task, double delay, void *frame);
/* The wait_for outcome after a resume: 0 the task completed, 1 the deadline
   fired first (the caller raises TimeoutError, the task was cancelled).
   Clears the frame's rows either way. */
int64_t ps_task_wait_outcome(void *frame);

/* ps_async.c: the asyncio primitives (Lock, Event, Semaphore, Queue) and
   `to_thread`. The wait is cooperative: the primitive parks the running
   task and the loop re-queues it when the primitive releases the waiter. */
void *ps_alock_new(void);
int64_t ps_alock_acquire(void *box);
void ps_alock_release(void *box);
int32_t ps_alock_locked(void *box);
void *ps_aevent_new(void);
int64_t ps_aevent_wait(void *box);
void ps_aevent_set(void *box);
void ps_aevent_clear(void *box);
int32_t ps_aevent_is_set(void *box);
void *ps_asem_new(int64_t value);
int64_t ps_asem_acquire(void *box);
void ps_asem_release(void *box);
int64_t ps_asem_value(void *box);
void *ps_aqueue_new(int64_t maxsize, int64_t elem);
int64_t ps_aqueue_put(void *box, void *src);
int64_t ps_aqueue_take(void *box);
void ps_aqueue_taken(void *box, int64_t size, void *out);
int64_t ps_aqueue_qsize(void *box);
int32_t ps_aqueue_empty(void *box);
int32_t ps_aqueue_full(void *box);
/* `to_thread`: the record owns the result slot; the worker stores the
   callable's result into it and marks the record done. */
void *ps_to_thread_new(int64_t size);
void ps_to_thread_start(void *box, void *pair);
void ps_to_thread_store(void *box, void *src, int64_t size);
int64_t ps_to_thread_poll(void *box);
void ps_to_thread_result(void *box, int64_t size, void *out);
void ps_to_thread_pump(void);
void ps_to_thread_park(void *box);
int32_t ps_threads_pending(void);

#endif
