/* HMAC-MD5, SHA-1, SHA-256, SHA-384, and SHA-512.

One call hashes the token. The block transform is the same code hashlib
already uses. compare_digest is constant time when the lengths match.
*/

#include "ps_rt.h"

#include <stdlib.h>
#include <string.h>

extern PsBuf *ps_hash_md5(int64_t n, char *p);
extern PsBuf *ps_hash_sha1(int64_t n, char *p);
extern PsBuf *ps_hash_sha256(int64_t n, char *p);
extern PsBuf *ps_hash_sha384(int64_t n, char *p);
extern PsBuf *ps_hash_sha512(int64_t n, char *p);

typedef struct PsHmac {
    PsBuf key;
    PsBuf msg;
    int64_t alg;
} PsHmac;

static PsBuf *(*ps_hmac_hash(int64_t alg))(int64_t, char *) {
    if (alg == 1) {
        return ps_hash_md5;
    }
    if (alg == 2) {
        return ps_hash_sha1;
    }
    if (alg == 3) {
        return ps_hash_sha256;
    }
    if (alg == 4) {
        return ps_hash_sha384;
    }
    if (alg == 5) {
        return ps_hash_sha512;
    }
    ps_raise_cstr(PS_VALUE, "hmac algorithm is md5, sha1, sha256, sha384, or sha512");
}

static int ps_hmac_block(int64_t alg) {
    if (alg == 4 || alg == 5) {
        return 128;
    }
    return 64;
}

static void ps_hmac_mac(int64_t alg, const char *key, int64_t kn, const char *msg, int64_t mn, PsBuf *out) {
    PsBuf *(*hash)(int64_t, char *) = ps_hmac_hash(alg);
    int block = ps_hmac_block(alg);
    uint8_t *pad;
    uint8_t *inner;
    uint8_t *keyed;
    PsBuf *core;
    PsBuf *digest;
    int i;
    if (kn > block) {
        core = hash(kn, (char *)key);
        kn = core->len;
        key = core->data;
    }
    keyed = malloc((size_t)block);
    inner = malloc((size_t)block + (size_t)mn);
    if (keyed == NULL || inner == NULL) {
        abort();
    }
    memset(keyed, 0, (size_t)block);
    if (kn > 0) {
        memcpy(keyed, key, (size_t)kn);
    }
    for (i = 0; i < block; i++) {
        inner[i] = (uint8_t)(keyed[i] ^ 0x36);
    }
    if (mn > 0) {
        memcpy(inner + block, msg, (size_t)mn);
    }
    core = hash((int64_t)block + mn, (char *)inner);
    pad = malloc((size_t)block + (size_t)core->len);
    if (pad == NULL) {
        abort();
    }
    for (i = 0; i < block; i++) {
        pad[i] = (uint8_t)(keyed[i] ^ 0x5c);
    }
    memcpy(pad + block, core->data, (size_t)core->len);
    digest = hash((int64_t)block + core->len, (char *)pad);
    ps_buf_copy(out, digest->data, digest->len);
    free(keyed);
    free(inner);
    free(pad);
}

void ps_hmac_digest(int64_t kn, const char *key, int64_t mn, const char *msg, int64_t alg, PsBuf *out) {
    ps_hmac_mac(alg, key, kn, msg, mn, out);
}

void ps_hmac_new(int64_t kn, const char *key, int64_t mn, const char *msg, int64_t alg, PsHmac *out) {
    ps_hmac_hash(alg);
    ps_buf_copy(&out->key, key, kn);
    ps_buf_copy(&out->msg, msg, mn);
    out->alg = alg;
}

void ps_hmac_finish(PsHmac *box, PsBuf *out) {
    ps_hmac_mac(box->alg, box->key.data, box->key.len, box->msg.data, box->msg.len, out);
}

int32_t ps_hmac_compare(int64_t an, const char *a, int64_t bn, const char *b) {
    unsigned char diff = 0;
    int64_t i;
    if (an != bn) {
        return 0;
    }
    for (i = 0; i < an; i++) {
        diff = (unsigned char)(diff | ((unsigned char)a[i] ^ (unsigned char)b[i]));
    }
    return diff == 0;
}
