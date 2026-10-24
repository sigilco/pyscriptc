/* pyscriptc-orjson thunks (pack-format spec section 11, the worked example).

The pack's only new C. Each thunk is a thin wrapper over the compiler
runtime's json writer/reader, so the pack shares one JSON implementation
with the stdlib json lane (spec open question 1, resolved toward sharing).

The option-flag mapping the in-tree lowering carried (orjson names onto the
writer's flag word: bit 0 sort keys, bits 8-15 indent, bit 16 compact) lives
here now:

    OPT_SORT_KEYS   -> PS_PACK_ORJSON_SORT_KEYS   (writer bit 0)
    OPT_INDENT_2    -> PS_PACK_ORJSON_INDENT_2    (writer indent field, 2 << 8)
    compact default -> PS_PACK_ORJSON_COMPACT     (writer bit 0x10000; orjson
                       dumps is compact, the writer's default is not)

Values cross as the ps_rt.h layout slots the writer and reader already use:
the value slot is 16 bytes (the largest lowered scalar / a pointer to a
PsSeq or PsMap), the type table is the %ps.json.ty shape (i32 kind, i32
child). */

#include <stdint.h>

#include "ps_rt.h"

#define PS_PACK_ORJSON_SORT_KEYS 1
#define PS_PACK_ORJSON_INDENT_2 (2 << 8)
#define PS_PACK_ORJSON_COMPACT 0x10000

/* The runtime entry points the thunks sit over, declared here because the
   runtime ships no header that names them (ps_json.c declares its own). */
void ps_json_dumps_opts(const PsType *types, int32_t root, void *value, int32_t flags, PsBuf *out);
void ps_json_loads(int64_t n, char *p, const PsType *types, int32_t root, void *out);

/* The writer flags a compiled `dumps` call passes: compact by default, with
   the caller's orjson options mapped onto the writer's word. */
int32_t ps_pack_orjson_flags(int32_t option) {
    return PS_PACK_ORJSON_COMPACT | option;
}

void ps_pack_orjson_dumps(const PsType *types, int32_t root, void *value, int32_t option, PsBuf *out) {
    ps_json_dumps_opts(types, root, value, PS_PACK_ORJSON_COMPACT | (option & (PS_PACK_ORJSON_SORT_KEYS | PS_PACK_ORJSON_INDENT_2)), out);
}

void ps_pack_orjson_loads(int64_t n, char *p, const PsType *types, int32_t root, void *out) {
    ps_json_loads(n, p, types, root, out);
}
