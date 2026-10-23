/* pyscriptc-jwt pack: the HS256/HS384/HS512 sign and verify subset of
PyJWT 2.15.1 (issue #61, the closed subset in restlower/_jwt.py).

jwt.encode(payload, key, algorithm="HS256") is the PyJWT HS* path: a fixed
`{"alg":..., "typ":"JWT"}` header sorted by key (`alg` first), the payload
dumps compact (`separators=(",", ":")`), base64url without padding, `.`-joined,
HMAC-SHA2 over `header.payload`. jwt.decode(token, key, algorithms) checks
the segment count, the alg allow-list and the signature with compare_digest,
then applies the `exp` numeric check. RS256 and the other asymmetric
algorithms need bignum and stay fenced; errors raise ValueError where PyJWT
raises its own InvalidTokenError hierarchy. The payload is dict[str, str]:
claims beyond str values (int exp, list aud) are outside the subset.
*/

#include "ps_rt.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

extern void ps_b64_url_encode(int64_t n, char *p, PsBuf *out);
extern void ps_b64_url_decode(int64_t n, char *p, PsBuf *out);
extern void ps_hmac_digest(int64_t kn, const char *key, int64_t mn, const char *msg, int64_t alg, PsBuf *out);
extern int32_t ps_hmac_compare(int64_t an, const char *a, int64_t bn, const char *b);
extern PsMap *ps_map_new(int32_t key_kind, int64_t key_size, int64_t val_size, int64_t cap);

static int ps_jwt_alg(int64_t n, const char *p) {
    if (n == 5 && memcmp(p, "HS256", 5) == 0) {
        return 3;
    }
    if (n == 5 && memcmp(p, "HS384", 5) == 0) {
        return 4;
    }
    if (n == 5 && memcmp(p, "HS512", 5) == 0) {
        return 5;
    }
    return 0;
}

/* base64url without padding, as PyJWT's base64url_encode. */
static void ps_jwt_b64url(int64_t n, const char *p, PsBuf *out) {
    ps_b64_url_encode(n, (char *)p, out);
    int64_t len = out->len;
    while (len > 0 && out->data[len - 1] == '=') {
        len--;
    }
    out->len = len;
    out->data[len] = '\0';
}

static void ps_jwt_b64url_decode(int64_t n, const char *p, PsBuf *out) {
    int64_t rem = n - (n / 4) * 4;
    int64_t total = n + (rem == 0 ? 0 : 4 - rem);
    char *padded = malloc((size_t)total + 1);
    if (padded == NULL) {
        abort();
    }
    memcpy(padded, p, (size_t)n);
    for (int64_t i = n; i < total; i++) {
        padded[i] = '=';
    }
    ps_b64_url_decode(total, padded, out);
    free(padded);
}

/* json.dumps of one str with the compact separators and CPython's escaping:
   the lowered dumps emits the same escapes. */
static void ps_jwt_quote(int64_t n, const char *p, PsGrow *g) {
    ps_grow_byte(g, '"');
    for (int64_t i = 0; i < n; i++) {
        unsigned char c = (unsigned char)p[i];
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

/* The header JSON. PyJWT dumps {"typ": "JWT", "alg": name} with
   sort_keys=True, so `alg` sorts first and the rest is constant. */
static void ps_jwt_header(int64_t an, const char *alg, PsBuf *out) {
    PsGrow g;
    ps_grow_init(&g);
    ps_grow_add(&g, "{\"alg\":\"", 8);
    ps_grow_add(&g, alg, an);
    ps_grow_add(&g, "\",\"typ\":\"JWT\"}", 14);
    ps_grow_finish(&g, out);
}

/* The payload JSON: compact dumps of dict[str, str] with sorted keys.
   PyJWT dumps insertion-ordered, but the subset's dict is a hash map with no
   stable order, so the pack emits claims sorted; decode does not depend on
   the order, and the token bytes for a multi-claim payload differ from
   CPython's in order only. */
static void ps_jwt_claims(PsMap *payload, PsGrow *g) {
    int64_t n = ps_map_len(payload);
    int64_t *order = NULL;
    if (n > 1) {
        order = malloc((size_t)n * sizeof(int64_t));
        if (order == NULL) {
            abort();
        }
        for (int64_t i = 0; i < n; i++) {
            order[i] = i;
        }
        for (int64_t i = 1; i < n; i++) {
            int64_t take = order[i];
            int64_t j = i - 1;
            PsBuf key;
            ps_map_key_at(payload, take, &key);
            while (j >= 0) {
                PsBuf prev;
                ps_map_key_at(payload, order[j], &prev);
                size_t cmp_len = (size_t)(key.len < prev.len ? key.len : prev.len);
                int cmp = memcmp(key.data, prev.data, cmp_len);
                if (cmp > 0 || (cmp == 0 && key.len >= prev.len)) {
                    break;
                }
                order[j + 1] = order[j];
                j -= 1;
            }
            order[j + 1] = take;
        }
    }
    ps_grow_byte(g, '{');
    for (int64_t i = 0; i < n; i++) {
        if (i > 0) {
            ps_grow_byte(g, ',');
        }
        PsBuf key;
        ps_map_key_at(payload, order ? order[i] : i, &key);
        ps_jwt_quote(key.len, key.data, g);
        ps_grow_byte(g, ':');
        PsBuf val;
        ps_map_val_at(payload, order ? order[i] : i, &val);
        ps_jwt_quote(val.len, val.data, g);
    }
    ps_grow_byte(g, '}');
    free(order);
}

void ps_jwt_encode(PsMap *payload, int64_t kn, const char *key, int64_t an, const char *alg, PsBuf *out) {
    int code = ps_jwt_alg(an, alg);
    if (code == 0) {
        ps_raise_cstr(PS_VALUE, "jwt algorithm is HS256, HS384, or HS512");
    }
    PsBuf head;
    ps_jwt_header(an, alg, &head);
    PsGrow g;
    ps_grow_init(&g);
    ps_jwt_claims(payload, &g);
    PsBuf claims;
    ps_grow_finish(&g, &claims);
    PsBuf hb;
    ps_jwt_b64url(head.len, head.data, &hb);
    PsBuf cb;
    ps_jwt_b64url(claims.len, claims.data, &cb);
    char *signing = malloc((size_t)hb.len + (size_t)cb.len + 2);
    if (signing == NULL) {
        abort();
    }
    memcpy(signing, hb.data, (size_t)hb.len);
    signing[hb.len] = '.';
    memcpy(signing + hb.len + 1, cb.data, (size_t)cb.len);
    int64_t slen = hb.len + 1 + cb.len;
    PsBuf sig;
    ps_hmac_digest(kn, key, slen, signing, code, &sig);
    PsBuf sb;
    ps_jwt_b64url(sig.len, sig.data, &sb);
    PsGrow outg;
    ps_grow_init(&outg);
    ps_grow_add(&outg, signing, slen);
    ps_grow_byte(&outg, '.');
    ps_grow_add(&outg, sb.data, sb.len);
    ps_grow_finish(&outg, out);
    free(signing);
    free(hb.data);
    free(cb.data);
    free(sig.data);
    free(sb.data);
    free(head.data);
    free(claims.data);
}

/* One JWT header field as (key, value) views, or 0 when the object ended.
   Only `alg` matters to the subset, so the parser keeps just the shape. */
static int ps_jwt_header_alg(int64_t n, const char *p, int64_t *an, const char **ap) {
    int64_t i = 0;
    while (i < n && (p[i] == ' ' || p[i] == '\t')) {
        i++;
    }
    if (i >= n || p[i] != '{') {
        return 0;
    }
    i++;
    for (;;) {
        while (i < n && (p[i] == ' ' || p[i] == '\t' || p[i] == ',')) {
            i++;
        }
        if (i < n && p[i] == '}') {
            return 0;
        }
        if (i >= n || p[i] != '"') {
            return 0;
        }
        i++;
        int64_t ks = i;
        while (i < n && p[i] != '"') {
            i++;
        }
        if (i >= n) {
            return 0;
        }
        int64_t klen = i - ks;
        i++;
        while (i < n && (p[i] == ' ' || p[i] == ':')) {
            i++;
        }
        if (i >= n || p[i] != '"') {
            return 0;
        }
        i++;
        int64_t vs = i;
        while (i < n && p[i] != '"') {
            i++;
        }
        if (i >= n) {
            return 0;
        }
        int64_t vlen = i - vs;
        i++;
        if (klen == 3 && memcmp(p + ks, "alg", 3) == 0) {
            *an = vlen;
            *ap = p + vs;
            return 1;
        }
    }
}

static void ps_jwt_b64_join(int64_t hn, const char *head, int64_t pn, const char *payload, PsBuf *out) {
    /* base64url segments carry no padding, so decode re-pads. The signing
       input is the raw segment text. */
    PsGrow g;
    ps_grow_init(&g);
    ps_grow_add(&g, head, hn);
    ps_grow_byte(&g, '.');
    ps_grow_add(&g, payload, pn);
    ps_grow_finish(&g, out);
}

PsMap *ps_jwt_decode(int64_t tn, const char *token, int64_t kn, const char *key, PsSeq *algorithms) {
    PsMap *out = ps_map_new(2, 16, 16, 0);
    /* Split into the three segments. */
    int64_t dots[2];
    int ndots = 0;
    for (int64_t i = 0; i < tn; i++) {
        if (token[i] == '.') {
            if (ndots == 2) {
                ps_raise_cstr(PS_VALUE, "Not enough segments");
            }
            dots[ndots++] = i;
        }
    }
    if (ndots != 2 || dots[0] == 0 || dots[1] == dots[0] + 1 || dots[1] + 1 == tn) {
        ps_raise_cstr(PS_VALUE, "Not enough segments");
    }
    int64_t hn = dots[0];
    const char *hp = token;
    int64_t pn = dots[1] - dots[0] - 1;
    const char *pp = token + dots[0] + 1;
    int64_t sn = tn - dots[1] - 1;
    const char *sp = token + dots[1] + 1;

    PsBuf head_raw;
    ps_jwt_b64url_decode(hn, hp, &head_raw);
    int64_t an = 0;
    const char *ap = NULL;
    if (!ps_jwt_header_alg(head_raw.len, head_raw.data, &an, &ap)) {
        free(head_raw.data);
        ps_raise_cstr(PS_VALUE, "Invalid header string");
    }
    int code = ps_jwt_alg(an, ap);
    char alg_name[8];
    if (code != 0 && an > 0 && an <= 5) {
        memcpy(alg_name, ap, (size_t)an);
    }
    free(head_raw.data);
    if (code == 0) {
        ps_raise_cstr(PS_VALUE, "Algorithm not supported");
    }
    int allowed = 0;
    for (int64_t i = 0; i < algorithms->len; i++) {
        PsBuf want;
        ps_seq_get(algorithms, i, &want);
        if (want.len == an && memcmp(want.data, alg_name, (size_t)an) == 0) {
            allowed = 1;
            break;
        }
    }
    if (!allowed) {
        ps_raise_cstr(PS_VALUE, "The specified alg value is not allowed");
    }

    PsBuf signing;
    ps_jwt_b64_join(hn, hp, pn, pp, &signing);
    PsBuf expected;
    ps_hmac_digest(kn, key, signing.len, signing.data, code, &expected);
    PsBuf actual;
    ps_jwt_b64url_decode(sn, sp, &actual);
    int ok = actual.len == expected.len && ps_hmac_compare(actual.len, actual.data, expected.len, expected.data);
    free(signing.data);
    free(expected.data);
    free(actual.data);
    if (!ok) {
        ps_raise_cstr(PS_VALUE, "Signature verification failed");
    }

    /* The claims: compact JSON of dict[str, str] read through the runtime
       JSON string parser, then put into the result map. */
    PsBuf claims_raw;
    ps_jwt_b64url_decode(pn, pp, &claims_raw);
    int64_t i = 0;
    int64_t n = claims_raw.len;
    const char *p = claims_raw.data;
    while (i < n && (p[i] == ' ' || p[i] == '\t')) {
        i++;
    }
    if (i >= n || p[i] != '{') {
        free(claims_raw.data);
        ps_raise_cstr(PS_VALUE, "Invalid payload string");
    }
    i++;
    for (;;) {
        while (i < n && (p[i] == ' ' || p[i] == '\t' || p[i] == ',')) {
            i++;
        }
        if (i < n && p[i] == '}') {
            break;
        }
        if (i >= n || p[i] != '"') {
            free(claims_raw.data);
            ps_raise_cstr(PS_VALUE, "Invalid payload string");
        }
        i++;
        PsGrow kg;
        ps_grow_init(&kg);
        while (i < n && p[i] != '"') {
            if (p[i] == '\\' && i + 1 < n) {
                i++;
                char e = p[i++];
                if (e == 'n') {
                    ps_grow_byte(&kg, '\n');
                } else if (e == 't') {
                    ps_grow_byte(&kg, '\t');
                } else if (e == 'r') {
                    ps_grow_byte(&kg, '\r');
                } else if (e == 'b') {
                    ps_grow_byte(&kg, '\b');
                } else if (e == 'f') {
                    ps_grow_byte(&kg, '\f');
                } else {
                    ps_grow_byte(&kg, e);
                }
            } else {
                ps_grow_byte(&kg, (unsigned char)p[i++]);
            }
        }
        i++;
        while (i < n && (p[i] == ' ' || p[i] == ':')) {
            i++;
        }
        if (i >= n || p[i] != '"') {
            free(kg.data);
            free(claims_raw.data);
            ps_raise_cstr(PS_VALUE, "Invalid payload string");
        }
        i++;
        PsGrow vg;
        ps_grow_init(&vg);
        while (i < n && p[i] != '"') {
            if (p[i] == '\\' && i + 1 < n) {
                i++;
                char e = p[i++];
                if (e == 'n') {
                    ps_grow_byte(&vg, '\n');
                } else if (e == 't') {
                    ps_grow_byte(&vg, '\t');
                } else if (e == 'r') {
                    ps_grow_byte(&vg, '\r');
                } else if (e == 'b') {
                    ps_grow_byte(&vg, '\b');
                } else if (e == 'f') {
                    ps_grow_byte(&vg, '\f');
                } else {
                    ps_grow_byte(&vg, e);
                }
            } else {
                ps_grow_byte(&vg, (unsigned char)p[i++]);
            }
        }
        i++;
        PsBuf k;
        ps_grow_finish(&kg, &k);
        PsBuf v;
        ps_grow_finish(&vg, &v);
        /* The map stores the PsBuf struct, so it owns the grow buffers:
           freeing them here would leave every claim dangling. */
        ps_map_put(out, &k, &v);
    }
    free(claims_raw.data);
    return out;
}
