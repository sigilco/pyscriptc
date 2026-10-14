/* struct.pack / struct.unpack for a literal format. Standard sizes.
'@' is refused by the lowerer. Out of range values are ValueError. */

#include "ps_rt.h"

#include <stdlib.h>
#include <string.h>

static int ps_little(void) {
    uint16_t x = 1;
    return *(unsigned char *)&x == 1;
}

static int ps_code_size(char code) {
    switch (code) {
    case 'x':
    case 'b':
    case 'B':
    case 's':
        return 1;
    case 'h':
    case 'H':
        return 2;
    case 'i':
    case 'I':
    case 'l':
    case 'L':
    case 'f':
        return 4;
    case 'q':
    case 'Q':
    case 'd':
        return 8;
    default:
        return -1;
    }
}

static int ps_code_kind(char code) {
    if (code == 'x') {
        return 0;
    }
    if (code == 'f' || code == 'd') {
        return 2;
    }
    if (code == 's') {
        return 3;
    }
    return 1;
}

static void ps_put_bytes(unsigned char *dst, uint64_t v, int size, int le) {
    for (int i = 0; i < size; i++) {
        int shift = le ? i : (size - 1 - i);
        dst[i] = (unsigned char)((v >> (8 * shift)) & 0xff);
    }
}

static uint64_t ps_get_bytes(const unsigned char *src, int size, int le) {
    uint64_t v = 0;
    for (int i = 0; i < size; i++) {
        int shift = le ? i : (size - 1 - i);
        v |= (uint64_t)src[i] << (8 * shift);
    }
    return v;
}

static int ps_in_range(char code, int64_t v) {
    switch (code) {
    case 'b':
        return v >= -128 && v <= 127;
    case 'B':
        return v >= 0 && v <= 255;
    case 'h':
        return v >= -32768 && v <= 32767;
    case 'H':
        return v >= 0 && v <= 65535;
    case 'i':
    case 'l':
        return v >= -2147483648LL && v <= 2147483647LL;
    case 'I':
    case 'L':
        return v >= 0 && v <= 4294967295LL;
    case 'q':
        return 1;
    case 'Q':
        return v >= 0;
    default:
        return 0;
    }
}

static void ps_walk(const char *fmt, int64_t fn, int pack, PsArg *args, int64_t nargs, const char *data, int64_t dn,
                    PsBuf *packed, PsSeq **unpacked, int want) {
    int le = ps_little();
    int64_t i = 0;
    int64_t argi = 0;
    int64_t cursor = 0;
    PsGrow g;
    PsSeq *seq = NULL;
    if (pack) {
        ps_grow_init(&g);
    } else {
        int64_t elem = want == 2 ? 8 : want == 3 ? 16 : 8;
        seq = ps_seq_new(elem, 0);
    }
    while (i < fn) {
        char c = fmt[i];
        if (c == ' ' || c == '\t' || c == '\n' || c == '\r') {
            i++;
            continue;
        }
        if (c == '<' ) {
            le = 1;
            i++;
            continue;
        }
        if (c == '>' || c == '!') {
            le = 0;
            i++;
            continue;
        }
        if (c == '=') {
            le = ps_little();
            i++;
            continue;
        }
        if (c == '@') {
            ps_raise_cstr(PS_VALUE, "struct native alignment is not lowered");
        }
        int64_t count = 1;
        if (c >= '0' && c <= '9') {
            count = 0;
            while (i < fn && fmt[i] >= '0' && fmt[i] <= '9') {
                count = count * 10 + (fmt[i] - '0');
                i++;
            }
            if (i >= fn) {
                ps_raise_cstr(PS_VALUE, "bad struct format");
            }
            c = fmt[i];
        }
        i++;
        int size = ps_code_size(c);
        if (size < 0) {
            ps_raise_cstr(PS_VALUE, "bad struct format");
        }
        int kind = ps_code_kind(c);
        if (!pack && kind != 0 && want != 0 && kind != want) {
            ps_raise_cstr(PS_VALUE, "struct.unpack result types are mixed");
        }
        if (c == 's') {
            int64_t nbytes = count;
            if (pack) {
                if (argi >= nargs || args[argi].kind != 3) {
                    ps_raise_cstr(PS_VALUE, "struct.pack expected bytes");
                }
                PsBuf b = args[argi++].b;
                int64_t take = b.len < nbytes ? b.len : nbytes;
                if (take > 0) {
                    ps_grow_add(&g, b.data, take);
                }
                for (int64_t z = take; z < nbytes; z++) {
                    ps_grow_byte(&g, 0);
                }
            } else {
                if (cursor + nbytes > dn) {
                    ps_raise_cstr(PS_VALUE, "struct.unpack buffer is too small");
                }
                PsBuf piece;
                ps_buf_copy(&piece, data + cursor, nbytes);
                ps_seq_push(seq, &piece);
                cursor += nbytes;
            }
            continue;
        }
        for (int64_t k = 0; k < count; k++) {
            if (c == 'x') {
                if (pack) {
                    ps_grow_byte(&g, 0);
                } else {
                    if (cursor + 1 > dn) {
                        ps_raise_cstr(PS_VALUE, "struct.unpack buffer is too small");
                    }
                    cursor += 1;
                }
                continue;
            }
            if (pack) {
                if (argi >= nargs) {
                    ps_raise_cstr(PS_VALUE, "struct.pack is missing an argument");
                }
                unsigned char tmp[8];
                if (kind == 1) {
                    if (args[argi].kind != 1) {
                        ps_raise_cstr(PS_VALUE, "struct.pack expected an int");
                    }
                    int64_t v = args[argi++].i;
                    if (!ps_in_range(c, v)) {
                        ps_raise_cstr(PS_VALUE, "struct format argument out of range");
                    }
                    ps_put_bytes(tmp, (uint64_t)v, size, le);
                    ps_grow_add(&g, (char *)tmp, size);
                } else {
                    double dv;
                    if (args[argi].kind == 2) {
                        dv = args[argi++].f;
                    } else if (args[argi].kind == 1) {
                        dv = (double)args[argi++].i;
                    } else {
                        ps_raise_cstr(PS_VALUE, "struct.pack expected a float");
                    }
                    if (c == 'f') {
                        float f = (float)dv;
                        uint32_t bits = 0;
                        memcpy(&bits, &f, 4);
                        ps_put_bytes(tmp, bits, 4, le);
                        ps_grow_add(&g, (char *)tmp, 4);
                    } else {
                        uint64_t bits = 0;
                        memcpy(&bits, &dv, 8);
                        ps_put_bytes(tmp, bits, 8, le);
                        ps_grow_add(&g, (char *)tmp, 8);
                    }
                }
            } else {
                if (cursor + size > dn) {
                    ps_raise_cstr(PS_VALUE, "struct.unpack buffer is too small");
                }
                uint64_t bits = ps_get_bytes((const unsigned char *)data + cursor, size, le);
                cursor += size;
                if (kind == 1) {
                    int64_t v = 0;
                    if (c == 'b') {
                        v = (int8_t)bits;
                    } else if (c == 'B') {
                        v = (uint8_t)bits;
                    } else if (c == 'h') {
                        v = (int16_t)bits;
                    } else if (c == 'H') {
                        v = (uint16_t)bits;
                    } else if (c == 'i' || c == 'l') {
                        v = (int32_t)bits;
                    } else if (c == 'I' || c == 'L') {
                        v = (uint32_t)bits;
                    } else if (c == 'q') {
                        v = (int64_t)bits;
                    } else {
                        if (bits > (uint64_t)INT64_MAX) {
                            ps_raise_cstr(PS_VALUE, "struct Q result does not fit in i64");
                        }
                        v = (int64_t)bits;
                    }
                    ps_seq_push(seq, &v);
                } else if (c == 'f') {
                    uint32_t u = (uint32_t)bits;
                    float f = 0;
                    memcpy(&f, &u, 4);
                    double d = (double)f;
                    ps_seq_push(seq, &d);
                } else {
                    double d = 0;
                    memcpy(&d, &bits, 8);
                    ps_seq_push(seq, &d);
                }
            }
        }
    }
    if (pack) {
        if (argi != nargs) {
            free(g.data);
            ps_raise_cstr(PS_VALUE, "struct.pack has too many arguments");
        }
        ps_grow_finish(&g, packed);
        return;
    }
    if (cursor != dn) {
        ps_raise_cstr(PS_VALUE, "struct.unpack buffer has trailing bytes");
    }
    *unpacked = seq;
}

void ps_struct_pack(int64_t fn, char *fmt, PsArg *args, int64_t nargs, PsBuf *out) {
    ps_walk(fmt, fn, 1, args, nargs, NULL, 0, out, NULL, 0);
}

PsSeq *ps_struct_unpack(int64_t fn, char *fmt, int64_t dn, char *data, int32_t want) {
    PsSeq *seq = NULL;
    ps_walk(fmt, fn, 0, NULL, 0, data, dn, NULL, &seq, want);
    return seq;
}
