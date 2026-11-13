/* secrets: the cryptographically strong token helpers.

`token_hex(nbytes)` returns the lowercase hexadecimal text of `nbytes` random
bytes; `token_bytes(nbytes)` returns the bytes themselves. `randbelow(n)`
returns an int in `[0, n)` drawn from the same entropy source, the way
CPython's `secrets` does: it draws `n.bit_length()` bits and redraws while
the draw is out of range, so the result is uniform.

The entropy comes from the host's `/dev/urandom`, the source CPython's
`SystemRandom` uses through `os.urandom`.
*/

#include "ps_rt.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* `nbytes` random bytes from the host entropy source. */
static void ps_secrets_bytes(int64_t n, unsigned char *out) {
    if (n < 0) {
        ps_raise_cstr(PS_VALUE, "negative count");
    }
    FILE *random = fopen("/dev/urandom", "rb");
    if (random == NULL) {
        ps_raise_cstr(PS_OSERROR, "secrets: no entropy source");
    }
    size_t got = fread(out, 1, (size_t)n, random);
    fclose(random);
    if (got != (size_t)n) {
        ps_raise_cstr(PS_OSERROR, "secrets: no entropy source");
    }
}

/* `randbelow(n)`: an int in [0, n), drawn the way CPython's
   `SystemRandom._randbelow` draws it. */
int64_t ps_secrets_randbelow(int64_t n) {
    if (n <= 0) {
        ps_raise_cstr(PS_VALUE, "Upper bound must be positive.");
    }
    int64_t bits = 0;
    for (int64_t v = n; v > 0; v >>= 1) {
        bits++;
    }
    int64_t bytes = (bits + 7) / 8;
    unsigned char buf[8];
    if (bytes > (int64_t)sizeof(buf)) {
        ps_raise_cstr(PS_OVERFLOW, "secrets: bound does not fit");
    }
    for (;;) {
        ps_secrets_bytes(bytes, buf);
        uint64_t draw = 0;
        for (int64_t i = 0; i < bytes; i++) {
            draw = (draw << 8) | (uint64_t)buf[i];
        }
        /* Trim the excess high bits, the way CPython's getrandbits does. */
        int64_t excess = bytes * 8 - bits;
        draw >>= (uint64_t)excess;
        if ((int64_t)draw < n) {
            return (int64_t)draw;
        }
    }
}

/* `token_bytes(nbytes)`: the bytes. */
void ps_secrets_token_bytes(int64_t n, PsBuf *out) {
    if (n < 0) {
        ps_raise_cstr(PS_VALUE, "negative count");
    }
    char *q = malloc((size_t)n + 1);
    if (q == NULL) {
        abort();
    }
    ps_secrets_bytes(n, (unsigned char *)q);
    q[n] = '\0';
    out->len = n;
    out->data = q;
}

/* `token_hex(nbytes)`: the lowercase hexadecimal text of the bytes. */
void ps_secrets_token_hex(int64_t n, PsBuf *out) {
    static const char kHex[] = "0123456789abcdef";
    if (n < 0) {
        ps_raise_cstr(PS_VALUE, "negative count");
    }
    if (n > INT64_MAX / 2) {
        ps_raise_cstr(PS_OVERFLOW, "secrets: result does not fit");
    }
    char *q = malloc((size_t)n * 2 + 1);
    if (q == NULL) {
        abort();
    }
    unsigned char *raw = malloc((size_t)n + 1);
    if (raw == NULL) {
        abort();
    }
    ps_secrets_bytes(n, raw);
    int64_t w = 0;
    for (int64_t i = 0; i < n; i++) {
        q[w++] = kHex[(raw[i] >> 4) & 15];
        q[w++] = kHex[raw[i] & 15];
    }
    q[w] = '\0';
    free(raw);
    out->len = w;
    out->data = q;
}
