/* The F1c aggregate fixture: a thunk that returns a list of strings and a
 * dict of string to string, built with the ps_rt.h append-only layout.
 *
 * This is the adapter side of the manifest contract: the library allocates
 * the aggregates through the runtime helpers (ps_seq_new/ps_seq_set,
 * ps_map_new/ps_map_put) and hands them to the compiled caller, which owns
 * them from the return on. A string crosses the boundary as a PsBuf
 * ({ i64 len, char *data }), the same shape the emitter spells %ps.str.
 *
 * The manifest binds the abi thunks here; the c_symbol entries are
 * documentation. The test links this file through link_binary's
 * compile_units seam next to the runtime.
 */

#include <stdint.h>
#include <stdlib.h>
#include <string.h>

/* The runtime pieces this thunk needs, spelled out so the file compiles
   without the runtime include path. PsBuf matches %ps.str on this ABI. */
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

PsSeq *ps_seq_new(int64_t elem, int64_t n);
void ps_seq_set(PsSeq *s, int64_t i, const void *src);
PsMap *ps_map_new(int32_t key_kind, int64_t key_size, int64_t val_size, int64_t cap);
void ps_map_put(PsMap *m, void *key, void *val);

static PsBuf ffx_str(const char *text) {
    int64_t n = (int64_t)strlen(text);
    char *data = malloc((size_t)n);
    if (data == NULL) {
        abort();
    }
    memcpy(data, text, (size_t)n);
    PsBuf out;
    out.len = n;
    out.data = data;
    return out;
}

/* names() -> list[str]: three entries, in insertion order. The thunk
   allocates the seq with its final length: ps_seq_new(elem, n) sets len to
   n, so a later ps_seq_push on this seq would append past the rows the
   caller sees. The three PsBuf values are malloc'd; ownership passes to
   the caller with the return. */
PsSeq *ps_ffi_ffx_names(void) {
    PsSeq *seq = ps_seq_new((int64_t)sizeof(PsBuf), 3);
    static const char *entries[3] = {"alpha", "beta", "gamma"};
    for (int64_t i = 0; i < 3; i++) {
        PsBuf value = ffx_str(entries[i]);
        ps_seq_set(seq, i, &value);
    }
    return seq;
}

/* row() -> dict[str, str]: two keys, name first. */
PsMap *ps_ffi_ffx_row(void) {
    PsMap *map = ps_map_new(2, (int64_t)sizeof(PsBuf), (int64_t)sizeof(PsBuf), 2);
    PsBuf key = ffx_str("name");
    PsBuf value = ffx_str("ada");
    ps_map_put(map, &key, &value);
    PsBuf key2 = ffx_str("city");
    PsBuf value2 = ffx_str("lyon");
    ps_map_put(map, &key2, &value2);
    return map;
}

/* rows() -> list[dict[str, str]]: the sqlite pilot's row shape, two records
   that both carry a name key. The list stores PsMap pointers, so the elem
   size is the pointer size. */
PsSeq *ps_ffi_ffx_rows(void) {
    PsSeq *seq = ps_seq_new((int64_t)sizeof(PsMap *), 2);
    PsMap *second = ps_map_new(2, (int64_t)sizeof(PsBuf), (int64_t)sizeof(PsBuf), 1);
    PsBuf key2 = ffx_str("name");
    PsBuf value2 = ffx_str("bo");
    ps_map_put(second, &key2, &value2);
    PsMap *first = ps_ffi_ffx_row();
    ps_seq_set(seq, 0, &first);
    ps_seq_set(seq, 1, &second);
    return seq;
}
