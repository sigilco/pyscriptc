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
    PS_EXCEPTION = 7
};

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

extern char ps_empty_byte;

__attribute__((noreturn)) void ps_raise_cstr(int32_t code, const char *msg);
__attribute__((noreturn)) void ps_raise_code(int32_t code);
__attribute__((noreturn)) void ps_reraise(void);
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

#endif
