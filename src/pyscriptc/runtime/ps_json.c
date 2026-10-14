/* json.loads / json.dumps for int, bool, str, list, and dict[str, ...].
Float text and null are ValueError. Object key order follows insertion. */

#include "ps_rt.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct PsJson {
    const char *p;
    int64_t n;
    int64_t i;
    const PsType *types;
} PsJson;

static void ps_json_fail(const char *msg) { ps_raise_cstr(PS_VALUE, msg); }

static void ps_skip(PsJson *j) {
    while (j->i < j->n) {
        char c = j->p[j->i];
        if (c != ' ' && c != '\t' && c != '\n' && c != '\r') {
            break;
        }
        j->i++;
    }
}

static char ps_peek(PsJson *j) {
    ps_skip(j);
    if (j->i >= j->n) {
        ps_json_fail("unexpected end of JSON");
    }
    return j->p[j->i];
}

static int ps_hex_digit(char c) {
    if (c >= '0' && c <= '9') {
        return c - '0';
    }
    if (c >= 'a' && c <= 'f') {
        return c - 'a' + 10;
    }
    if (c >= 'A' && c <= 'F') {
        return c - 'A' + 10;
    }
    return -1;
}

static void ps_parse_string(PsJson *j, PsBuf *out) {
    if (ps_peek(j) != '"') {
        ps_json_fail("JSON string expected");
    }
    j->i++;
    PsGrow g;
    ps_grow_init(&g);
    while (j->i < j->n) {
        unsigned char c = (unsigned char)j->p[j->i++];
        if (c == '"') {
            ps_grow_finish(&g, out);
            return;
        }
        if (c == '\\') {
            if (j->i >= j->n) {
                free(g.data);
                ps_json_fail("bad JSON string escape");
            }
            char e = j->p[j->i++];
            if (e == '"' || e == '\\' || e == '/') {
                ps_grow_byte(&g, (unsigned char)e);
            } else if (e == 'b') {
                ps_grow_byte(&g, '\b');
            } else if (e == 'f') {
                ps_grow_byte(&g, '\f');
            } else if (e == 'n') {
                ps_grow_byte(&g, '\n');
            } else if (e == 'r') {
                ps_grow_byte(&g, '\r');
            } else if (e == 't') {
                ps_grow_byte(&g, '\t');
            } else if (e == 'u') {
                int cp = 0;
                for (int k = 0; k < 4; k++) {
                    if (j->i >= j->n) {
                        free(g.data);
                        ps_json_fail("bad JSON unicode escape");
                    }
                    int d = ps_hex_digit(j->p[j->i++]);
                    if (d < 0) {
                        free(g.data);
                        ps_json_fail("bad JSON unicode escape");
                    }
                    cp = (cp << 4) | d;
                }
                if (cp > 127) {
                    free(g.data);
                    ps_json_fail("non-ASCII JSON string is not lowered");
                }
                ps_grow_byte(&g, (unsigned char)cp);
            } else {
                free(g.data);
                ps_json_fail("bad JSON string escape");
            }
            continue;
        }
        if (c < 0x20) {
            free(g.data);
            ps_json_fail("unescaped control character in JSON string");
        }
        if (c > 127) {
            free(g.data);
            ps_json_fail("non-ASCII JSON string is not lowered");
        }
        ps_grow_byte(&g, c);
    }
    free(g.data);
    ps_json_fail("unterminated JSON string");
}

static void ps_parse_int(PsJson *j, int64_t *out) {
    ps_skip(j);
    int neg = 0;
    if (j->i < j->n && j->p[j->i] == '-') {
        neg = 1;
        j->i++;
    }
    if (j->i >= j->n || j->p[j->i] < '0' || j->p[j->i] > '9') {
        ps_json_fail("JSON number expected");
    }
    if (j->p[j->i] == '0') {
        j->i++;
        if (j->i < j->n && j->p[j->i] >= '0' && j->p[j->i] <= '9') {
            ps_json_fail("JSON number has a leading zero");
        }
        if (j->i < j->n && (j->p[j->i] == '.' || j->p[j->i] == 'e' || j->p[j->i] == 'E')) {
            ps_json_fail("float JSON is not lowered");
        }
        *out = 0;
        return;
    }
    uint64_t mag = 0;
    while (j->i < j->n && j->p[j->i] >= '0' && j->p[j->i] <= '9') {
        int digit = j->p[j->i] - '0';
        if (mag > (UINT64_MAX - (uint64_t)digit) / 10) {
            ps_json_fail("JSON integer does not fit in i64");
        }
        mag = mag * 10 + (uint64_t)digit;
        j->i++;
    }
    if (j->i < j->n && (j->p[j->i] == '.' || j->p[j->i] == 'e' || j->p[j->i] == 'E')) {
        ps_json_fail("float JSON is not lowered");
    }
    if (!neg) {
        if (mag > (uint64_t)INT64_MAX) {
            ps_json_fail("JSON integer does not fit in i64");
        }
        *out = (int64_t)mag;
        return;
    }
    if (mag > (uint64_t)INT64_MAX + 1) {
        ps_json_fail("JSON integer does not fit in i64");
    }
    if (mag == (uint64_t)INT64_MAX + 1) {
        *out = INT64_MIN;
        return;
    }
    *out = -(int64_t)mag;
}

static int64_t ps_slot(int kind) {
    if (kind == PS_JSON_INT) {
        return 8;
    }
    if (kind == PS_JSON_BOOL) {
        return 4;
    }
    if (kind == PS_JSON_STR) {
        return 16;
    }
    return 8;
}

static void ps_parse_value(PsJson *j, int32_t idx, void *out);

static void ps_expect(PsJson *j, char c) {
    if (ps_peek(j) != c) {
        ps_json_fail("JSON value does not match the annotation");
    }
    j->i++;
}

static void ps_parse_value(PsJson *j, int32_t idx, void *out) {
    PsType typ = j->types[idx];
    char c = ps_peek(j);
    if (c == 'n' && j->i + 4 <= j->n && memcmp(j->p + j->i, "null", 4) == 0) {
        ps_json_fail("JSON null is not lowered");
    }
    if (typ.kind == PS_JSON_INT) {
        int64_t v = 0;
        ps_parse_int(j, &v);
        memcpy(out, &v, 8);
        return;
    }
    if (typ.kind == PS_JSON_BOOL) {
        if (j->i + 4 <= j->n && memcmp(j->p + j->i, "true", 4) == 0) {
            j->i += 4;
            int32_t v = 1;
            memcpy(out, &v, 4);
            return;
        }
        if (j->i + 5 <= j->n && memcmp(j->p + j->i, "false", 5) == 0) {
            j->i += 5;
            int32_t v = 0;
            memcpy(out, &v, 4);
            return;
        }
        ps_json_fail("JSON bool expected");
    }
    if (typ.kind == PS_JSON_STR) {
        ps_parse_string(j, out);
        return;
    }
    if (typ.kind == PS_JSON_LIST) {
        ps_expect(j, '[');
        PsType child = j->types[typ.child];
        PsSeq *seq = ps_seq_new(ps_slot(child.kind), 0);
        ps_skip(j);
        if (j->i < j->n && j->p[j->i] == ']') {
            j->i++;
            memcpy(out, &seq, 8);
            return;
        }
        for (;;) {
            unsigned char slot[16];
            memset(slot, 0, sizeof slot);
            ps_parse_value(j, typ.child, slot);
            ps_seq_push(seq, slot);
            ps_skip(j);
            if (j->i < j->n && j->p[j->i] == ',') {
                j->i++;
                continue;
            }
            if (j->i < j->n && j->p[j->i] == ']') {
                j->i++;
                break;
            }
            ps_json_fail("JSON array is not closed");
        }
        memcpy(out, &seq, 8);
        return;
    }
    if (typ.kind == PS_JSON_DICT) {
        ps_expect(j, '{');
        PsType child = j->types[typ.child];
        PsMap *map = ps_map_new(2, 16, ps_slot(child.kind), 0);
        ps_skip(j);
        if (j->i < j->n && j->p[j->i] == '}') {
            j->i++;
            memcpy(out, &map, 8);
            return;
        }
        for (;;) {
            PsBuf key;
            ps_parse_string(j, &key);
            ps_skip(j);
            if (j->i >= j->n || j->p[j->i] != ':') {
                ps_json_fail("JSON object expects ':'");
            }
            j->i++;
            unsigned char slot[16];
            memset(slot, 0, sizeof slot);
            ps_parse_value(j, typ.child, slot);
            ps_map_put(map, &key, slot);
            ps_skip(j);
            if (j->i < j->n && j->p[j->i] == ',') {
                j->i++;
                continue;
            }
            if (j->i < j->n && j->p[j->i] == '}') {
                j->i++;
                break;
            }
            ps_json_fail("JSON object is not closed");
        }
        memcpy(out, &map, 8);
        return;
    }
    ps_json_fail("JSON type is not lowered");
}

void ps_json_loads(int64_t n, char *p, const PsType *types, int32_t root, void *out) {
    PsJson j = {p, n, 0, types};
    ps_parse_value(&j, root, out);
    ps_skip(&j);
    if (j.i != j.n) {
        ps_json_fail("trailing JSON");
    }
}

static void ps_dump(const PsType *types, int32_t idx, const void *value, PsGrow *g);

static void ps_dump_str(PsBuf buf, PsGrow *g) {
    ps_grow_byte(g, '"');
    for (int64_t i = 0; i < buf.len; i++) {
        unsigned char c = (unsigned char)buf.data[i];
        if (c == '"' || c == '\\') {
            ps_grow_byte(g, '\\');
            ps_grow_byte(g, c);
        } else if (c == '\b') {
            ps_grow_add(g, "\\b", 2);
        } else if (c == '\f') {
            ps_grow_add(g, "\\f", 2);
        } else if (c == '\n') {
            ps_grow_add(g, "\\n", 2);
        } else if (c == '\r') {
            ps_grow_add(g, "\\r", 2);
        } else if (c == '\t') {
            ps_grow_add(g, "\\t", 2);
        } else if (c < 0x20) {
            char tmp[7];
            snprintf(tmp, sizeof tmp, "\\u%04x", c);
            ps_grow_add(g, tmp, 6);
        } else {
            ps_grow_byte(g, c);
        }
    }
    ps_grow_byte(g, '"');
}

static void ps_dump(const PsType *types, int32_t idx, const void *value, PsGrow *g) {
    PsType typ = types[idx];
    if (typ.kind == PS_JSON_INT) {
        int64_t v = 0;
        memcpy(&v, value, 8);
        char tmp[32];
        int wrote = snprintf(tmp, sizeof tmp, "%lld", (long long)v);
        if (wrote < 0) {
            ps_json_fail("cannot format JSON integer");
        }
        ps_grow_add(g, tmp, wrote);
        return;
    }
    if (typ.kind == PS_JSON_BOOL) {
        int32_t v = 0;
        memcpy(&v, value, 4);
        if (v) {
            ps_grow_add(g, "true", 4);
        } else {
            ps_grow_add(g, "false", 5);
        }
        return;
    }
    if (typ.kind == PS_JSON_STR) {
        PsBuf buf;
        memcpy(&buf, value, 16);
        ps_dump_str(buf, g);
        return;
    }
    if (typ.kind == PS_JSON_LIST) {
        PsSeq *seq = NULL;
        memcpy(&seq, value, 8);
        PsType child = types[typ.child];
        ps_grow_byte(g, '[');
        for (int64_t i = 0; i < seq->len; i++) {
            if (i > 0) {
                ps_grow_add(g, ", ", 2);
            }
            ps_dump(types, typ.child, (char *)seq->data + (size_t)i * (size_t)seq->elem, g);
        }
        ps_grow_byte(g, ']');
        (void)child;
        return;
    }
    if (typ.kind == PS_JSON_DICT) {
        PsMap *map = NULL;
        memcpy(&map, value, 8);
        ps_grow_byte(g, '{');
        for (int64_t i = 0; i < ps_map_len(map); i++) {
            if (i > 0) {
                ps_grow_add(g, ", ", 2);
            }
            PsBuf key;
            ps_map_key_at(map, i, &key);
            ps_dump_str(key, g);
            ps_grow_add(g, ": ", 2);
            unsigned char slot[16];
            memset(slot, 0, sizeof slot);
            ps_map_val_at(map, i, slot);
            ps_dump(types, typ.child, slot, g);
        }
        ps_grow_byte(g, '}');
        return;
    }
    ps_json_fail("JSON type is not lowered");
}

void ps_json_dumps(const PsType *types, int32_t root, void *value, PsBuf *out) {
    PsGrow g;
    ps_grow_init(&g);
    ps_dump(types, root, value, &g);
    ps_grow_finish(&g, out);
}
