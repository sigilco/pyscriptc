/* binascii: the hexadecimal codec.

`unhexlify` decodes an even-length hexadecimal string into bytes; `hexlify`
encodes bytes into a lowercase hexadecimal string. Both are the CPython
behaviour: the decoder rejects an odd length and a non-hex character with
`binascii.Error`, which CPython also raises as a `ValueError` subclass, so
the runtime raises the ValueError code.
*/

#include "ps_rt.h"

#include <stdlib.h>
#include <string.h>

static int ps_bin_hexval(char c) {
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

/* `unhexlify(text)`: the bytes the hex text spells.

   CPython accepts only ASCII hex; a non-ASCII byte is an error too. The
   decoder writes into a buffer of half the input length. */
void ps_bin_unhexlify(int64_t n, const char *p, PsBuf *out) {
    if (n < 0) {
        ps_raise_cstr(PS_VALUE, "binascii length is negative");
    }
    if (n % 2 != 0) {
        ps_raise_cstr(PS_VALUE, "binascii.Error: odd-length string");
    }
    char *q = malloc((size_t)n / 2 + 1);
    if (q == NULL) {
        abort();
    }
    int64_t w = 0;
    for (int64_t i = 0; i < n; i += 2) {
        int hi = ps_bin_hexval(p[i]);
        int lo = ps_bin_hexval(p[i + 1]);
        if (hi < 0 || lo < 0) {
            free(q);
            ps_raise_cstr(PS_VALUE, "binascii.Error: Non-hexadecimal digit found");
        }
        q[w++] = (char)((hi << 4) | lo);
    }
    out->len = w;
    out->data = q;
}

/* `hexlify(data)`: the lowercase hex text of the bytes. */
void ps_bin_hexlify(int64_t n, const char *p, PsBuf *out) {
    static const char kHex[] = "0123456789abcdef";
    if (n < 0) {
        ps_raise_cstr(PS_VALUE, "binascii length is negative");
    }
    if (n > INT64_MAX / 2) {
        ps_raise_cstr(PS_OVERFLOW, "binascii result does not fit");
    }
    char *q = malloc((size_t)n * 2 + 1);
    if (q == NULL) {
        abort();
    }
    int64_t w = 0;
    for (int64_t i = 0; i < n; i++) {
        unsigned char c = (unsigned char)p[i];
        q[w++] = kHex[(c >> 4) & 15];
        q[w++] = kHex[c & 15];
    }
    q[w] = '\0';
    out->len = w;
    out->data = q;
}
