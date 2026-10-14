/* md5, sha1, sha256, sha384, and sha512. No OpenSSL. The result is an owned
digest buffer; hexdigest formats it. */

#include "ps_rt.h"

#include <stdlib.h>
#include <string.h>

static uint32_t ps_rotr32(uint32_t x, int n) { return (x >> n) | (x << (32 - n)); }
static uint64_t ps_rotr64(uint64_t x, int n) { return (x >> n) | (x << (64 - n)); }
static uint32_t ps_rotl32(uint32_t x, int n) { return (x << n) | (x >> (32 - n)); }

static uint32_t ps_be32(const uint8_t *p) {
    return ((uint32_t)p[0] << 24) | ((uint32_t)p[1] << 16) | ((uint32_t)p[2] << 8) | (uint32_t)p[3];
}

static uint32_t ps_le32(const uint8_t *p) {
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

/* ps_be64 needs the low half without recursion. */
static uint64_t ps_load_be64(const uint8_t *p) {
    return ((uint64_t)ps_be32(p) << 32) | (uint64_t)ps_be32(p + 4);
}

static void ps_store_be32(uint8_t *p, uint32_t v) {
    p[0] = (uint8_t)(v >> 24);
    p[1] = (uint8_t)(v >> 16);
    p[2] = (uint8_t)(v >> 8);
    p[3] = (uint8_t)v;
}

static void ps_store_be64(uint8_t *p, uint64_t v) {
    ps_store_be32(p, (uint32_t)(v >> 32));
    ps_store_be32(p + 4, (uint32_t)v);
}

static void ps_store_le32(uint8_t *p, uint32_t v) {
    p[0] = (uint8_t)v;
    p[1] = (uint8_t)(v >> 8);
    p[2] = (uint8_t)(v >> 16);
    p[3] = (uint8_t)(v >> 24);
}

static PsBuf *ps_digest(const uint8_t *bytes, int64_t n) {
    PsBuf *box = ps_alloc((int64_t)sizeof(PsBuf));
    ps_buf_copy(box, (const char *)bytes, n);
    return box;
}

static void ps_md5_block(uint32_t h[4], const uint8_t block[64]) {
    static const int s[64] = {7, 12, 17, 22, 7, 12, 17, 22, 7, 12, 17, 22, 7, 12, 17, 22, 5, 9,  14, 20, 5, 9,
                              14, 20, 5,  9,  14, 20, 5, 9,  14, 20, 4, 11, 16, 23, 4, 11, 16, 23, 4,  11, 16, 23,
                              4, 11, 16, 23, 6,  10, 15, 21, 6, 10, 15, 21, 6, 10, 15, 21, 6, 10, 15, 21};
    uint32_t m[16];
    for (int i = 0; i < 16; i++) {
        m[i] = ps_le32(block + (size_t)i * 4);
    }
    uint32_t a = h[0];
    uint32_t b = h[1];
    uint32_t c = h[2];
    uint32_t d = h[3];
    for (int i = 0; i < 64; i++) {
        uint32_t f;
        int g;
        if (i < 16) {
            f = (b & c) | (~b & d);
            g = i;
        } else if (i < 32) {
            f = (d & b) | (~d & c);
            g = (5 * i + 1) % 16;
        } else if (i < 48) {
            f = b ^ c ^ d;
            g = (3 * i + 5) % 16;
        } else {
            f = c ^ (b | ~d);
            g = (7 * i) % 16;
        }
        double sine = __builtin_sin((double)(i + 1));
        if (sine < 0) {
            sine = -sine;
        }
        uint32_t t = (uint32_t)(sine * 4294967296.0);
        uint32_t next = b + ps_rotl32(a + f + t + m[g], s[i]);
        a = d;
        d = c;
        c = b;
        b = next;
    }
    h[0] += a;
    h[1] += b;
    h[2] += c;
    h[3] += d;
}

static PsBuf *ps_md5_of(const char *p, int64_t n) {
    uint32_t h[4] = {0x67452301u, 0xefcdab89u, 0x98badcfeu, 0x10325476u};
    uint8_t block[64];
    int64_t off = 0;
    while (off + 64 <= n) {
        ps_md5_block(h, (const uint8_t *)p + off);
        off += 64;
    }
    int64_t rem = n - off;
    memset(block, 0, 64);
    if (rem > 0) {
        memcpy(block, p + off, (size_t)rem);
    }
    block[rem] = 0x80;
    if (rem >= 56) {
        ps_md5_block(h, block);
        memset(block, 0, 64);
    }
    uint64_t bits = (uint64_t)n * 8;
    for (int i = 0; i < 8; i++) {
        block[56 + i] = (uint8_t)(bits >> (8 * i));
    }
    ps_md5_block(h, block);
    uint8_t out[16];
    for (int i = 0; i < 4; i++) {
        ps_store_le32(out + i * 4, h[i]);
    }
    return ps_digest(out, 16);
}

static void ps_sha1_block(uint32_t h[5], const uint8_t block[64]) {
    uint32_t w[80];
    for (int i = 0; i < 16; i++) {
        w[i] = ps_be32(block + (size_t)i * 4);
    }
    for (int i = 16; i < 80; i++) {
        w[i] = ps_rotl32(w[i - 3] ^ w[i - 8] ^ w[i - 14] ^ w[i - 16], 1);
    }
    uint32_t a = h[0];
    uint32_t b = h[1];
    uint32_t c = h[2];
    uint32_t d = h[3];
    uint32_t e = h[4];
    for (int i = 0; i < 80; i++) {
        uint32_t f;
        uint32_t k;
        if (i < 20) {
            f = (b & c) | (~b & d);
            k = 0x5a827999u;
        } else if (i < 40) {
            f = b ^ c ^ d;
            k = 0x6ed9eba1u;
        } else if (i < 60) {
            f = (b & c) | (b & d) | (c & d);
            k = 0x8f1bbcdcu;
        } else {
            f = b ^ c ^ d;
            k = 0xca62c1d6u;
        }
        uint32_t temp = ps_rotl32(a, 5) + f + e + k + w[i];
        e = d;
        d = c;
        c = ps_rotl32(b, 30);
        b = a;
        a = temp;
    }
    h[0] += a;
    h[1] += b;
    h[2] += c;
    h[3] += d;
    h[4] += e;
}

static PsBuf *ps_sha1_of(const char *p, int64_t n) {
    uint32_t h[5] = {0x67452301u, 0xefcdab89u, 0x98badcfeu, 0x10325476u, 0xc3d2e1f0u};
    uint8_t block[64];
    int64_t off = 0;
    while (off + 64 <= n) {
        ps_sha1_block(h, (const uint8_t *)p + off);
        off += 64;
    }
    int64_t rem = n - off;
    memset(block, 0, 64);
    if (rem > 0) {
        memcpy(block, p + off, (size_t)rem);
    }
    block[rem] = 0x80;
    if (rem >= 56) {
        ps_sha1_block(h, block);
        memset(block, 0, 64);
    }
    uint64_t bits = (uint64_t)n * 8;
    for (int i = 0; i < 8; i++) {
        block[56 + i] = (uint8_t)(bits >> (8 * (7 - i)));
    }
    ps_sha1_block(h, block);
    uint8_t out[20];
    for (int i = 0; i < 5; i++) {
        ps_store_be32(out + i * 4, h[i]);
    }
    return ps_digest(out, 20);
}

static const uint32_t PS_K256[64] = {
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5, 0xd807aa98, 0x12835b01,
    0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc,
    0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da, 0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147,
    0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070, 0x19a4c116, 0x1e376c08,
    0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3, 0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208,
    0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2};

static void ps_sha256_block(uint32_t st[8], const uint8_t block[64]) {
    uint32_t w[64];
    for (int i = 0; i < 16; i++) {
        w[i] = ps_be32(block + (size_t)i * 4);
    }
    for (int i = 16; i < 64; i++) {
        uint32_t s0 = ps_rotr32(w[i - 15], 7) ^ ps_rotr32(w[i - 15], 18) ^ (w[i - 15] >> 3);
        uint32_t s1 = ps_rotr32(w[i - 2], 17) ^ ps_rotr32(w[i - 2], 19) ^ (w[i - 2] >> 10);
        w[i] = w[i - 16] + s0 + w[i - 7] + s1;
    }
    uint32_t a = st[0];
    uint32_t b = st[1];
    uint32_t c = st[2];
    uint32_t d = st[3];
    uint32_t e = st[4];
    uint32_t f = st[5];
    uint32_t g = st[6];
    uint32_t hh = st[7];
    for (int i = 0; i < 64; i++) {
        uint32_t S1 = ps_rotr32(e, 6) ^ ps_rotr32(e, 11) ^ ps_rotr32(e, 25);
        uint32_t ch = (e & f) ^ (~e & g);
        uint32_t t1 = hh + S1 + ch + PS_K256[i] + w[i];
        uint32_t S0 = ps_rotr32(a, 2) ^ ps_rotr32(a, 13) ^ ps_rotr32(a, 22);
        uint32_t maj = (a & b) ^ (a & c) ^ (b & c);
        uint32_t t2 = S0 + maj;
        hh = g;
        g = f;
        f = e;
        e = d + t1;
        d = c;
        c = b;
        b = a;
        a = t1 + t2;
    }
    st[0] += a;
    st[1] += b;
    st[2] += c;
    st[3] += d;
    st[4] += e;
    st[5] += f;
    st[6] += g;
    st[7] += hh;
}

static PsBuf *ps_sha256_of(const char *p, int64_t n) {
    uint32_t st[8] = {0x6a09e667u, 0xbb67ae85u, 0x3c6ef372u, 0xa54ff53au, 0x510e527fu, 0x9b05688cu, 0x1f83d9abu, 0x5be0cd19u};
    uint8_t block[64];
    int64_t off = 0;
    while (off + 64 <= n) {
        ps_sha256_block(st, (const uint8_t *)p + off);
        off += 64;
    }
    int64_t rem = n - off;
    memset(block, 0, 64);
    if (rem > 0) {
        memcpy(block, p + off, (size_t)rem);
    }
    block[rem] = 0x80;
    if (rem >= 56) {
        ps_sha256_block(st, block);
        memset(block, 0, 64);
    }
    uint64_t bits = (uint64_t)n * 8;
    for (int i = 0; i < 8; i++) {
        block[56 + i] = (uint8_t)(bits >> (8 * (7 - i)));
    }
    ps_sha256_block(st, block);
    uint8_t out[32];
    for (int i = 0; i < 8; i++) {
        ps_store_be32(out + i * 4, st[i]);
    }
    return ps_digest(out, 32);
}

static const uint64_t PS_K512[80] = {
    0x428a2f98d728ae22ULL, 0x7137449123ef65cdULL, 0xb5c0fbcfec4d3b2fULL, 0xe9b5dba58189dbbcULL, 0x3956c25bf348b538ULL,
    0x59f111f1b605d019ULL, 0x923f82a4af194f9bULL, 0xab1c5ed5da6d8118ULL, 0xd807aa98a3030242ULL, 0x12835b0145706fbeULL,
    0x243185be4ee4b28cULL, 0x550c7dc3d5ffb4e2ULL, 0x72be5d74f27b896fULL, 0x80deb1fe3b1696b1ULL, 0x9bdc06a725c71235ULL,
    0xc19bf174cf692694ULL, 0xe49b69c19ef14ad2ULL, 0xefbe4786384f25e3ULL, 0x0fc19dc68b8cd5b5ULL, 0x240ca1cc77ac9c65ULL,
    0x2de92c6f592b0275ULL, 0x4a7484aa6ea6e483ULL, 0x5cb0a9dcbd41fbd4ULL, 0x76f988da831153b5ULL, 0x983e5152ee66dfabULL,
    0xa831c66d2db43210ULL, 0xb00327c898fb213fULL, 0xbf597fc7beef0ee4ULL, 0xc6e00bf33da88fc2ULL, 0xd5a79147930aa725ULL,
    0x06ca6351e003826fULL, 0x142929670a0e6e70ULL, 0x27b70a8546d22ffcULL, 0x2e1b21385c26c926ULL, 0x4d2c6dfc5ac42aedULL,
    0x53380d139d95b3dfULL, 0x650a73548baf63deULL, 0x766a0abb3c77b2a8ULL, 0x81c2c92e47edaee6ULL, 0x92722c851482353bULL,
    0xa2bfe8a14cf10364ULL, 0xa81a664bbc423001ULL, 0xc24b8b70d0f89791ULL, 0xc76c51a30654be30ULL, 0xd192e819d6ef5218ULL,
    0xd69906245565a910ULL, 0xf40e35855771202aULL, 0x106aa07032bbd1b8ULL, 0x19a4c116b8d2d0c8ULL, 0x1e376c085141ab53ULL,
    0x2748774cdf8eeb99ULL, 0x34b0bcb5e19b48a8ULL, 0x391c0cb3c5c95a63ULL, 0x4ed8aa4ae3418acbULL, 0x5b9cca4f7763e373ULL,
    0x682e6ff3d6b2b8a3ULL, 0x748f82ee5defb2fcULL, 0x78a5636f43172f60ULL, 0x84c87814a1f0ab72ULL, 0x8cc702081a6439ecULL,
    0x90befffa23631e28ULL, 0xa4506cebde82bde9ULL, 0xbef9a3f7b2c67915ULL, 0xc67178f2e372532bULL, 0xca273eceea26619cULL,
    0xd186b8c721c0c207ULL, 0xeada7dd6cde0eb1eULL, 0xf57d4f7fee6ed178ULL, 0x06f067aa72176fbaULL, 0x0a637dc5a2c898a6ULL,
    0x113f9804bef90daeULL, 0x1b710b35131c471bULL, 0x28db77f523047d84ULL, 0x32caab7b40c72493ULL, 0x3c9ebe0a15c9bebcULL,
    0x431d67c49c100d4cULL, 0x4cc5d4becb3e42b6ULL, 0x597f299cfc657e2aULL, 0x5fcb6fab3ad6faecULL, 0x6c44198c4a475817ULL};

static void ps_sha512_block(uint64_t st[8], const uint8_t block[128]) {
    uint64_t w[80];
    for (int i = 0; i < 16; i++) {
        w[i] = ps_load_be64(block + (size_t)i * 8);
    }
    for (int i = 16; i < 80; i++) {
        uint64_t s0 = ps_rotr64(w[i - 15], 1) ^ ps_rotr64(w[i - 15], 8) ^ (w[i - 15] >> 7);
        uint64_t s1 = ps_rotr64(w[i - 2], 19) ^ ps_rotr64(w[i - 2], 61) ^ (w[i - 2] >> 6);
        w[i] = w[i - 16] + s0 + w[i - 7] + s1;
    }
    uint64_t a = st[0];
    uint64_t b = st[1];
    uint64_t c = st[2];
    uint64_t d = st[3];
    uint64_t e = st[4];
    uint64_t f = st[5];
    uint64_t g = st[6];
    uint64_t hh = st[7];
    for (int i = 0; i < 80; i++) {
        uint64_t S1 = ps_rotr64(e, 14) ^ ps_rotr64(e, 18) ^ ps_rotr64(e, 41);
        uint64_t ch = (e & f) ^ (~e & g);
        uint64_t t1 = hh + S1 + ch + PS_K512[i] + w[i];
        uint64_t S0 = ps_rotr64(a, 28) ^ ps_rotr64(a, 34) ^ ps_rotr64(a, 39);
        uint64_t maj = (a & b) ^ (a & c) ^ (b & c);
        uint64_t t2 = S0 + maj;
        hh = g;
        g = f;
        f = e;
        e = d + t1;
        d = c;
        c = b;
        b = a;
        a = t1 + t2;
    }
    st[0] += a;
    st[1] += b;
    st[2] += c;
    st[3] += d;
    st[4] += e;
    st[5] += f;
    st[6] += g;
    st[7] += hh;
}

static void ps_sha512_pad(uint64_t st[8], const char *p, int64_t n) {
    uint8_t block[128];
    int64_t off = 0;
    while (off + 128 <= n) {
        ps_sha512_block(st, (const uint8_t *)p + off);
        off += 128;
    }
    int64_t rem = n - off;
    memset(block, 0, 128);
    if (rem > 0) {
        memcpy(block, p + off, (size_t)rem);
    }
    block[rem] = 0x80;
    if (rem >= 112) {
        ps_sha512_block(st, block);
        memset(block, 0, 128);
    }
    uint64_t bits = (uint64_t)n * 8;
    ps_store_be64(block + 112, 0);
    ps_store_be64(block + 120, bits);
    ps_sha512_block(st, block);
}

static PsBuf *ps_sha512_of(const char *p, int64_t n) {
    uint64_t st[8] = {0x6a09e667f3bcc908ULL, 0xbb67ae8584caa73bULL, 0x3c6ef372fe94f82bULL, 0xa54ff53a5f1d36f1ULL,
                      0x510e527fade682d1ULL, 0x9b05688c2b3e6c1fULL, 0x1f83d9abfb41bd6bULL, 0x5be0cd19137e2179ULL};
    ps_sha512_pad(st, p, n);
    uint8_t out[64];
    for (int i = 0; i < 8; i++) {
        ps_store_be64(out + i * 8, st[i]);
    }
    return ps_digest(out, 64);
}

static PsBuf *ps_sha384_of(const char *p, int64_t n) {
    uint64_t st[8] = {0xcbbb9d5dc1059ed8ULL, 0x629a292a367cd507ULL, 0x9159015a3070dd17ULL, 0x152fecd8f70e5939ULL,
                      0x67332667ffc00b31ULL, 0x8eb44a8768581511ULL, 0xdb0c2e0d64f98fa7ULL, 0x47b5481dbefa4fa4ULL};
    ps_sha512_pad(st, p, n);
    uint8_t out[48];
    for (int i = 0; i < 6; i++) {
        ps_store_be64(out + i * 8, st[i]);
    }
    return ps_digest(out, 48);
}

PsBuf *ps_hash_md5(int64_t n, char *p) { return ps_md5_of(p, n); }
PsBuf *ps_hash_sha1(int64_t n, char *p) { return ps_sha1_of(p, n); }
PsBuf *ps_hash_sha256(int64_t n, char *p) { return ps_sha256_of(p, n); }
PsBuf *ps_hash_sha384(int64_t n, char *p) { return ps_sha384_of(p, n); }
PsBuf *ps_hash_sha512(int64_t n, char *p) { return ps_sha512_of(p, n); }

void ps_hash_hex(PsBuf *h, PsBuf *out) {
    static const char *hexd = "0123456789abcdef";
    char *q = malloc((size_t)h->len * 2 + 1);
    if (q == NULL) {
        abort();
    }
    for (int64_t i = 0; i < h->len; i++) {
        unsigned char c = (unsigned char)h->data[i];
        q[i * 2] = hexd[c >> 4];
        q[i * 2 + 1] = hexd[c & 15];
    }
    q[h->len * 2] = '\0';
    out->len = h->len * 2;
    out->data = q;
}

void ps_hash_digest(PsBuf *h, PsBuf *out) { ps_buf_copy(out, h->data, h->len); }
