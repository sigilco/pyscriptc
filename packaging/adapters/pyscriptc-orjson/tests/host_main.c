/* Host harness for the pyscriptc-orjson thunk.

It runs the pack's own conformance sequences through the pack's thunk (the
C over the runtime's json writer/reader) and prints one canonical line per
case, so the differential test can compare against the real orjson under
CPython line by line. The harness uses the runtime layout directly (PsType
tables, PsMap, PsBuf) because the pack's thunk is the boundary the compiler
lowers onto, not a Python-callable surface.

Linked with the runtime C files (no island), the pack's thunk and this file. */

#include <stdio.h>

#include "ps_rt.h"

void ps_pack_orjson_dumps(const PsType *types, int32_t root, void *value, int32_t option, PsBuf *out);
void ps_pack_orjson_loads(int64_t n, char *p, const PsType *types, int32_t root, void *out);

/* The pack's option flags, as the compiler's lowering maps them. */
#define PS_PACK_ORJSON_SORT_KEYS 1
#define PS_PACK_ORJSON_INDENT_2 (2 << 8)

/* The type table for `dict[str, int]`: the shape the conformance suite's
   loads cases annotate. A dict's `child` is the value type; the key is
   always a str (the writer's key slot is a PsBuf). */
static const PsType TY_DICT_STR_INT[] = {
    {5, 1}, /* dict -> int */
    {1, 0}, /* int */
};

/* The type table for `dict[str, list[int]]`: the round-trip case. */
static const PsType TY_DICT_STR_LISTINT[] = {
    {5, 1}, /* dict -> list */
    {4, 2}, /* list -> int */
    {1, 0}, /* int */
};

static void print_buf(const char *label, PsBuf *buf) {
    printf("%s: %.*s\n", label, (int)buf->len, buf->data);
}

/* A `dict[str, int]` with two entries, built in the runtime's map layout. */
static PsMap *map_str_int(void) {
    PsMap *map = ps_map_new(3, (int64_t)sizeof(PsBuf), (int64_t)sizeof(int64_t), 2);
    PsBuf key;
    int64_t value;
    ps_buf_copy(&key, "a", 1);
    value = 1;
    ps_map_put(map, &key, &value);
    ps_buf_copy(&key, "b", 1);
    value = 2;
    ps_map_put(map, &key, &value);
    return map;
}

/* A `dict[str, list[int]]` with one entry. */
static PsMap *map_str_listint(void) {
    PsMap *map = ps_map_new(3, (int64_t)sizeof(PsBuf), (int64_t)sizeof(void *), 1);
    PsBuf key;
    PsSeq *seq = ps_seq_new((int64_t)sizeof(int64_t), 0);
    int64_t value = 10;
    ps_seq_push(seq, &value);
    value = 20;
    ps_seq_push(seq, &value);
    ps_buf_copy(&key, "k", 1);
    ps_map_put(map, &key, &seq);
    return map;
}

static void dump_case(const char *label, const PsType *types, int32_t root, void *value, int32_t option, PsBuf *out) {
    /* The writer reads the value out of a slot (the emitter stores the
       pointer there), so the slot holds the map pointer. */
    void *slot = value;
    ps_pack_orjson_dumps(types, root, &slot, option, out);
    print_buf(label, out);
}

int main(void) {
    PsBuf out;
    /* dumps_compact: the map keeps its insertion order. */
    dump_case("dumps_compact", TY_DICT_STR_INT, 0, map_str_int(), 0, &out);
    /* dumps_sorted: the writer sorts the keys. */
    dump_case("dumps_sorted", TY_DICT_STR_INT, 0, map_str_int(), PS_PACK_ORJSON_SORT_KEYS, &out);
    /* dumps_indent: the writer pads every item on its own line. */
    dump_case("dumps_indent", TY_DICT_STR_INT, 0, map_str_int(), PS_PACK_ORJSON_INDENT_2, &out);
    /* dumps_both_options: sorted and indented. */
    dump_case("dumps_both_options", TY_DICT_STR_INT, 0, map_str_int(), PS_PACK_ORJSON_SORT_KEYS | PS_PACK_ORJSON_INDENT_2, &out);
    /* loads_str: `{"a": 41}` read into a dict[str, int]. */
    {
        PsMap *map = NULL;
        ps_pack_orjson_loads((int64_t)(sizeof("{\"a\": 41}") - 1), "{\"a\": 41}", TY_DICT_STR_INT, 0, &map);
        int64_t cell = 0;
        ps_map_val_at(map, 0, &cell);
        printf("loads_str: %lld\n", (long long)cell);
    }
    /* loads_bytes: the same document read from a bytes buffer. */
    {
        PsMap *map = NULL;
        ps_pack_orjson_loads((int64_t)(sizeof("{\"a\": 40}") - 1), "{\"a\": 40}", TY_DICT_STR_INT, 0, &map);
        int64_t cell = 0;
        ps_map_val_at(map, 0, &cell);
        printf("loads_bytes: %lld\n", (long long)cell);
    }
    /* round_trip: the dumps output read back into a dict[str, list[int]]. */
    {
        dump_case("round_trip", TY_DICT_STR_LISTINT, 0, map_str_listint(), 0, &out);
        PsMap *map = NULL;
        ps_pack_orjson_loads(out.len, out.data, TY_DICT_STR_LISTINT, 0, &map);
        PsSeq *seq = NULL;
        ps_map_val_at(map, 0, &seq);
        int64_t first = 0;
        int64_t second = 0;
        ps_seq_get(seq, 0, &first);
        ps_seq_get(seq, 1, &second);
        printf("round_trip_sum: %lld\n", (long long)(first + second));
    }
    return 0;
}
