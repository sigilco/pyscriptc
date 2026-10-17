/* Shared layout for the runtime linked beside emitted LLVM.

%ps.str is { i64, ptr }. PsBuf must stay 16 bytes. PsSeq keeps len, elem, and
data in that order; cap is appended so existing stores still line up.
*/

#ifndef PYSCRIPTC_PS_RT_H
#define PYSCRIPTC_PS_RT_H

#include <stdint.h>

enum {
    PS_VALUE = 1,
    PS_INDEX = 2,
    PS_KEY = 3,
    PS_OVERFLOW = 4,
    PS_ZERO_DIV = 5,
    PS_RUNTIME = 6,
    PS_EXCEPTION = 7,
    PS_USER_BASE = 8
};

/* longjmp payload once an exception is pending; the class code lives on the
PsExc record, not on the wire. */
enum { PS_RAISED = -1 };

enum {
    PS_JSON_INT = 1,
    PS_JSON_BOOL = 2,
    PS_JSON_STR = 3,
    PS_JSON_LIST = 4,
    PS_JSON_DICT = 5
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
    int64_t len;
    int64_t cap;
    int32_t key_kind;
    int64_t key_size;
    int64_t val_size;
    void *keys;
    void *vals;
} PsMap;

typedef struct PsType {
    int32_t kind;
    int32_t child;
} PsType;

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
ps_msg channel. cause and args stay NULL until M3/E3. */
typedef struct PsExc {
    int32_t code;
    int32_t flags;
    PsBuf name;
    PsBuf message;
    struct PsExc *cause;
    PsSeq *args;
} PsExc;

extern char ps_empty_byte;

__attribute__((noreturn)) void ps_raise_cstr(int32_t code, const char *msg);
__attribute__((noreturn)) void ps_raise_code(int32_t code);
__attribute__((noreturn)) void ps_reraise(void);
int32_t ps_exc_code(void);
int32_t ps_exc_isa(int32_t raised, int32_t wanted);
void *ps_alloc(int64_t n);

PsSeq *ps_seq_new(int64_t elem, int64_t n);
int64_t ps_seq_len(PsSeq *s);
void ps_seq_get(PsSeq *s, int64_t i, void *out);
void ps_seq_set(PsSeq *s, int64_t i, void *src);
void ps_seq_push(PsSeq *s, const void *src);

PsMap *ps_map_new(int32_t key_kind, int64_t key_size, int64_t val_size, int64_t cap);
void ps_map_put(PsMap *m, void *key, void *val);
int64_t ps_map_len(PsMap *m);
void ps_map_key_at(PsMap *m, int64_t i, void *out);
PsSeq *ps_dict_keys(PsMap *m);
void ps_map_val_at(PsMap *m, int64_t i, void *out);

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

#endif
