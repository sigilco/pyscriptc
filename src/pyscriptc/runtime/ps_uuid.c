/* The closed uuid subset: uuid4, uuid1, uuid5 and the UUID class's text form.

   uuid4 draws 16 random bytes and sets the version (4) and variant (RFC 4122)
   bits, the way CPython's uuid4 does, and prints the canonical form
   "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx".

   uuid1 is the time-based UUID: the 60-bit timestamp counts 100-nanosecond
   intervals since 1582-10-15, and the node field carries a random 48-bit
   identifier. CPython uses the host MAC address where it can read one, and a
   random node otherwise; the node is an identifier either way, so a compiled
   program prints a different node than the host's CPython does.

   uuid5 is the name-based UUID over SHA-1: the namespace bytes and the name
   bytes are hashed, and the version (5) and variant bits are set, exactly as
   CPython's uuid5 does. The SHA-1 is computed here, over one buffer, the way
   CPython's uuid5 does (namespace bytes then name bytes).

   The UUID class itself is not lowered as a class: `UUID(text)` lowers to the
   canonical text, and `uuid5(namespace, name)` takes the namespace as that
   canonical text. The `.int`, `.urn` and `.bytes` forms stay refused. */

#include "ps_rt.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

static const char kHex[] = "0123456789abcdef";

static int ps_uuid_hexval(char c) {
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

/* The canonical form: 8-4-4-4-12 hex digits. */
static void ps_uuid_text(const unsigned char *b, PsBuf *out) {
    char *q = malloc(37);
    if (q == NULL) {
        abort();
    }
    static const int kGroup[5] = {4, 2, 2, 2, 6};
    int at = 0;
    int group = 0;
    int in_group = 0;
    for (int i = 0; i < 16; i++) {
        q[at++] = kHex[(b[i] >> 4) & 15];
        q[at++] = kHex[b[i] & 15];
        in_group++;
        if (group < 4 && in_group == kGroup[group]) {
            q[at++] = '-';
            group++;
            in_group = 0;
        }
    }
    q[at] = '\0';
    out->len = at;
    out->data = q;
}

static void ps_uuid_random(unsigned char *out, int64_t n) {
    FILE *random = fopen("/dev/urandom", "rb");
    if (random == NULL) {
        ps_raise_cstr(PS_OSERROR, "uuid: no entropy source");
    }
    size_t got = fread(out, 1, (size_t)n, random);
    fclose(random);
    if (got != (size_t)n) {
        ps_raise_cstr(PS_OSERROR, "uuid: no entropy source");
    }
}

/* The 60-bit count of 100-nanosecond intervals since 1582-10-15. */
static uint64_t ps_uuid_ticks(void) {
    struct timespec ts;
    if (clock_gettime(CLOCK_REALTIME, &ts) != 0) {
        ps_raise_cstr(PS_OSERROR, "uuid: the clock is not available");
    }
    /* 122192928000000000 ticks between 1582-10-15 and 1970-01-01. */
    uint64_t epoch = (uint64_t)ts.tv_sec * 10000000ULL + (uint64_t)(ts.tv_nsec / 100);
    return epoch + 122192928000000000ULL;
}

/* SHA-1 over one buffer: the name-based UUID's digest. */
static void ps_uuid_sha1(const unsigned char *p, int64_t n, unsigned char out[20]) {
    uint32_t h[5] = {0x67452301u, 0xefcdab89u, 0x98badcfeu, 0x10325476u, 0xc3d2e1f0u};
    uint32_t w[80];
    unsigned char block[64];
    int64_t off = 0;
    while (off + 64 <= n) {
        for (int i = 0; i < 16; i++) {
            w[i] = ((uint32_t)p[off + i * 4] << 24) | ((uint32_t)p[off + i * 4 + 1] << 16) |
                   ((uint32_t)p[off + i * 4 + 2] << 8) | (uint32_t)p[off + i * 4 + 3];
        }
        for (int i = 16; i < 80; i++) {
            uint32_t v = w[i - 3] ^ w[i - 8] ^ w[i - 14] ^ w[i - 16];
            w[i] = (v << 1) | (v >> 31);
        }
        uint32_t a = h[0], bb = h[1], c = h[2], d = h[3], e = h[4];
        for (int i = 0; i < 80; i++) {
            uint32_t f;
            uint32_t k;
            if (i < 20) {
                f = (bb & c) | ((~bb) & d);
                k = 0x5a827999u;
            } else if (i < 40) {
                f = bb ^ c ^ d;
                k = 0x6ed9eba1u;
            } else if (i < 60) {
                f = (bb & c) | (bb & d) | (c & d);
                k = 0x8f1bbcdcu;
            } else {
                f = bb ^ c ^ d;
                k = 0xca62c1d6u;
            }
            uint32_t tmp = ((a << 5) | (a >> 27)) + f + e + k + w[i];
            e = d;
            d = c;
            c = (bb << 30) | (bb >> 2);
            bb = a;
            a = tmp;
        }
        h[0] += a;
        h[1] += bb;
        h[2] += c;
        h[3] += d;
        h[4] += e;
        off += 64;
    }
    int64_t rem = n - off;
    memset(block, 0, 64);
    if (rem > 0) {
        memcpy(block, p + off, (size_t)rem);
    }
    block[rem] = 0x80;
    if (rem >= 56) {
        for (int i = 0; i < 16; i++) {
            w[i] = ((uint32_t)block[i * 4] << 24) | ((uint32_t)block[i * 4 + 1] << 16) |
                   ((uint32_t)block[i * 4 + 2] << 8) | (uint32_t)block[i * 4 + 3];
        }
        for (int i = 16; i < 80; i++) {
            uint32_t v = w[i - 3] ^ w[i - 8] ^ w[i - 14] ^ w[i - 16];
            w[i] = (v << 1) | (v >> 31);
        }
        uint32_t a = h[0], bb = h[1], c = h[2], d = h[3], e = h[4];
        for (int i = 0; i < 80; i++) {
            uint32_t f;
            uint32_t k;
            if (i < 20) {
                f = (bb & c) | ((~bb) & d);
                k = 0x5a827999u;
            } else if (i < 40) {
                f = bb ^ c ^ d;
                k = 0x6ed9eba1u;
            } else if (i < 60) {
                f = (bb & c) | (bb & d) | (c & d);
                k = 0x8f1bbcdcu;
            } else {
                f = bb ^ c ^ d;
                k = 0xca62c1d6u;
            }
            uint32_t tmp = ((a << 5) | (a >> 27)) + f + e + k + w[i];
            e = d;
            d = c;
            c = (bb << 30) | (bb >> 2);
            bb = a;
            a = tmp;
        }
        h[0] += a;
        h[1] += bb;
        h[2] += c;
        h[3] += d;
        h[4] += e;
        memset(block, 0, 64);
    }
    uint64_t bits = (uint64_t)n * 8;
    for (int i = 0; i < 8; i++) {
        block[56 + i] = (unsigned char)(bits >> (8 * (7 - i)));
    }
    for (int i = 0; i < 16; i++) {
        w[i] = ((uint32_t)block[i * 4] << 24) | ((uint32_t)block[i * 4 + 1] << 16) |
               ((uint32_t)block[i * 4 + 2] << 8) | (uint32_t)block[i * 4 + 3];
    }
    for (int i = 16; i < 80; i++) {
        uint32_t v = w[i - 3] ^ w[i - 8] ^ w[i - 14] ^ w[i - 16];
        w[i] = (v << 1) | (v >> 31);
    }
    uint32_t a = h[0], bb = h[1], c = h[2], d = h[3], e = h[4];
    for (int i = 0; i < 80; i++) {
        uint32_t f;
        uint32_t k;
        if (i < 20) {
            f = (bb & c) | ((~bb) & d);
            k = 0x5a827999u;
        } else if (i < 40) {
            f = bb ^ c ^ d;
            k = 0x6ed9eba1u;
        } else if (i < 60) {
            f = (bb & c) | (bb & d) | (c & d);
            k = 0x8f1bbcdcu;
        } else {
            f = bb ^ c ^ d;
            k = 0xca62c1d6u;
        }
        uint32_t tmp = ((a << 5) | (a >> 27)) + f + e + k + w[i];
        e = d;
        d = c;
        c = (bb << 30) | (bb >> 2);
        bb = a;
        a = tmp;
    }
    h[0] += a;
    h[1] += bb;
    h[2] += c;
    h[3] += d;
    h[4] += e;
    for (int i = 0; i < 5; i++) {
        out[i * 4] = (unsigned char)(h[i] >> 24);
        out[i * 4 + 1] = (unsigned char)(h[i] >> 16);
        out[i * 4 + 2] = (unsigned char)(h[i] >> 8);
        out[i * 4 + 3] = (unsigned char)h[i];
    }
}

void ps_uuid4(PsBuf *out) {
    unsigned char b[16];
    ps_uuid_random(b, 16);
    b[6] = (unsigned char)((b[6] & 0x0F) | 0x40);
    b[8] = (unsigned char)((b[8] & 0x3F) | 0x80);
    ps_uuid_text(b, out);
}

void ps_uuid1(PsBuf *out) {
    unsigned char b[16];
    uint64_t ticks = ps_uuid_ticks();
    for (int i = 7; i >= 0; i--) {
        b[i] = (unsigned char)(ticks & 0xFF);
        ticks >>= 8;
    }
    b[6] = (unsigned char)((b[6] & 0x0F) | 0x10);
    /* The clock sequence: a random 14-bit value, the way CPython picks one
       when it has no state to carry over. The sequence is 14 bits: the low
       six sit in b[8] under the two variant bits, the high eight in b[9]. */
    unsigned char seq[2];
    ps_uuid_random(seq, 2);
    b[8] = (unsigned char)((seq[0] & 0x3F) | 0x80);
    b[9] = (unsigned char)(seq[1] & 0xFF);
    /* The node: a random 48-bit identifier, with the multicast bit set the
       way CPython sets it for a random node. */
    unsigned char node[6];
    ps_uuid_random(node, 6);
    node[0] = (unsigned char)(node[0] | 0x01);
    for (int i = 0; i < 6; i++) {
        b[10 + i] = node[i];
    }
    ps_uuid_text(b, out);
}

/* The name-based UUID: SHA-1 over the namespace bytes and the name bytes. */
void ps_uuid5(int64_t nn, const char *np, int64_t nname, const char *namep, PsBuf *out) {
    if (nn != 36) {
        ps_raise_cstr(PS_VALUE, "badly formed hexadecimal UUID string");
    }
    unsigned char ns[16];
    int at = 0;
    for (int i = 0; i < 36; i++) {
        if (i == 8 || i == 13 || i == 18 || i == 23) {
            if (np[i] != '-') {
                ps_raise_cstr(PS_VALUE, "badly formed hexadecimal UUID string");
            }
            continue;
        }
        int hi = ps_uuid_hexval(np[i]);
        int lo = ps_uuid_hexval(np[i + 1]);
        if (hi < 0 || lo < 0) {
            ps_raise_cstr(PS_VALUE, "badly formed hexadecimal UUID string");
        }
        ns[at++] = (unsigned char)((hi << 4) | lo);
        i++;
    }
    if (at != 16) {
        ps_raise_cstr(PS_VALUE, "badly formed hexadecimal UUID string");
    }
    int64_t total = 16 + nname;
    unsigned char *joined = malloc((size_t)total);
    if (joined == NULL) {
        abort();
    }
    memcpy(joined, ns, 16);
    if (nname > 0) {
        memcpy(joined + 16, namep, (size_t)nname);
    }
    unsigned char digest[20];
    ps_uuid_sha1(joined, total, digest);
    free(joined);
    unsigned char b[16];
    memcpy(b, digest, 16);
    b[6] = (unsigned char)((b[6] & 0x0F) | 0x50);
    b[8] = (unsigned char)((b[8] & 0x3F) | 0x80);
    ps_uuid_text(b, out);
}

/* The UUID class as a canonical string: parse the text and print it back. */
void ps_uuid_parse(int64_t n, const char *p, PsBuf *out) {
    if (n != 36) {
        ps_raise_cstr(PS_VALUE, "badly formed hexadecimal UUID string");
    }
    unsigned char b[16];
    int at = 0;
    for (int i = 0; i < 36; i++) {
        if (i == 8 || i == 13 || i == 18 || i == 23) {
            if (p[i] != '-') {
                ps_raise_cstr(PS_VALUE, "badly formed hexadecimal UUID string");
            }
            continue;
        }
        int hi = ps_uuid_hexval(p[i]);
        int lo = ps_uuid_hexval(p[i + 1]);
        if (hi < 0 || lo < 0) {
            ps_raise_cstr(PS_VALUE, "badly formed hexadecimal UUID string");
        }
        b[at++] = (unsigned char)((hi << 4) | lo);
        i++;
    }
    if (at != 16) {
        ps_raise_cstr(PS_VALUE, "badly formed hexadecimal UUID string");
    }
    ps_uuid_text(b, out);
}

/* The `.hex` attribute of a lowered uuid call: the canonical text without
   the dashes, which is what CPython's UUID.hex holds. */
void ps_uuid_hex(int64_t n, const char *p, PsBuf *out) {
    char *q = malloc((size_t)n + 1);
    if (q == NULL) {
        abort();
    }
    int64_t at = 0;
    for (int64_t i = 0; i < n; i++) {
        if (p[i] == '-') {
            continue;
        }
        q[at++] = p[i];
    }
    ps_buf_copy(out, q, at);
    free(q);
}
