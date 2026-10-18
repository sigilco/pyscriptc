/* random: one shared MT19937 instance, seeded the way CPython's
   _randommodule seeds it — init_by_array over the int's little-endian
   32-bit limbs — so an int seed gives a CPython-identical stream.
   seed() with no argument is nondeterministic (time/clock/address mix). */
#include "ps_rt.h"

#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#define MT_N 624
#define MT_M 397

static uint32_t mt[MT_N];
static int mt_idx = MT_N;
static int mt_seeded = 0;

static void mt_init(uint32_t s) {
    mt[0] = s;
    for (int i = 1; i < MT_N; i++) {
        mt[i] = 1812433253U * (mt[i - 1] ^ (mt[i - 1] >> 30)) + (uint32_t)i;
    }
    mt_idx = MT_N;
}

static void mt_init_array(const uint32_t *key, int klen) {
    int i = 1;
    int j = 0;
    int k;
    mt_init(19650218U);
    k = MT_N > klen ? MT_N : klen;
    for (; k; k--) {
        mt[i] = (mt[i] ^ ((mt[i - 1] ^ (mt[i - 1] >> 30)) * 1664525U)) + key[j] + (uint32_t)j;
        i++;
        j++;
        if (i >= MT_N) {
            mt[0] = mt[MT_N - 1];
            i = 1;
        }
        if (j >= klen) {
            j = 0;
        }
    }
    for (k = MT_N - 1; k; k--) {
        mt[i] = (mt[i] ^ ((mt[i - 1] ^ (mt[i - 1] >> 30)) * 1566083941U)) - (uint32_t)i;
        i++;
        if (i >= MT_N) {
            mt[0] = mt[MT_N - 1];
            i = 1;
        }
    }
    mt[0] = 0x80000000U;
}

static void mt_twist(void) {
    static const uint32_t mag[2] = {0U, 0x9908b0dfU};
    int i;
    for (i = 0; i < MT_N - MT_M; i++) {
        uint32_t y = (mt[i] & 0x80000000U) | (mt[i + 1] & 0x7fffffffU);
        mt[i] = mt[i + MT_M] ^ (y >> 1) ^ mag[y & 1U];
    }
    for (; i < MT_N - 1; i++) {
        uint32_t y = (mt[i] & 0x80000000U) | (mt[i + 1] & 0x7fffffffU);
        mt[i] = mt[i + (MT_M - MT_N)] ^ (y >> 1) ^ mag[y & 1U];
    }
    {
        uint32_t y = (mt[MT_N - 1] & 0x80000000U) | (mt[0] & 0x7fffffffU);
        mt[MT_N - 1] = mt[MT_M - 1] ^ (y >> 1) ^ mag[y & 1U];
    }
    mt_idx = 0;
}

static uint32_t mt_next(void) {
    uint32_t y;
    if (mt_idx >= MT_N) {
        mt_twist();
    }
    y = mt[mt_idx++];
    y ^= y >> 11;
    y ^= (y << 7) & 0x9d2c5680U;
    y ^= (y << 15) & 0xefc60000U;
    y ^= y >> 18;
    return y;
}

void ps_rand_seed_auto(void) {
    uint64_t x = (uint64_t)time(NULL);
    uint32_t key[2];
    x = x * 0x9e3779b97f4a7c15ULL + 0x165667b19e3779f9ULL;
    x ^= (uint64_t)clock() << 32 | (uint64_t)clock();
    x ^= (uint64_t)(uintptr_t)&mt[0];
    key[0] = (uint32_t)(x & 0xffffffffU);
    key[1] = (uint32_t)(x >> 32);
    mt_init_array(key, 2);
    mt_seeded = 1;
}

void ps_rand_seed(int64_t v) {
    uint64_t u = v < 0 ? (uint64_t) ~ (uint64_t)v + 1 : (uint64_t)v;
    uint32_t key[2];
    int klen = 1;
    key[0] = (uint32_t)(u & 0xffffffffU);
    if (u >> 32) {
        key[1] = (uint32_t)(u >> 32);
        klen = 2;
    }
    mt_init_array(key, klen);
    mt_seeded = 1;
}

static uint32_t rand_gen(void) {
    if (!mt_seeded) {
        ps_rand_seed_auto();
    }
    return mt_next();
}

int64_t ps_rand_bits(int64_t k) {
    if (k < 0 || k > 64) {
        ps_raise_cstr(PS_VALUE, "number of bits must be 0 to 64");
    }
    if (k == 0) {
        return 0;
    }
    if (k <= 32) {
        return (int64_t)(rand_gen() >> (32 - k));
    }
    {
        uint64_t lo = rand_gen();
        uint64_t hi = rand_gen() >> (64 - k);
        return (int64_t)((hi << 32) | lo);
    }
}

double ps_rand_f64(void) {
    uint32_t a = rand_gen() >> 5;
    uint32_t b = rand_gen() >> 6;
    return ((double)a * 67108864.0 + (double)b) / 9007199254740992.0;
}

static int64_t rand_below(int64_t n) {
    int k = 0;
    uint64_t m = (uint64_t)n;
    uint64_t r;
    while (m != 0) {
        k++;
        m >>= 1;
    }
    do {
        r = (uint64_t)ps_rand_bits(k);
    } while (r >= (uint64_t)n);
    return (int64_t)r;
}

int64_t ps_rand_index(int64_t n) {
    if (n <= 0) {
        ps_raise_cstr(PS_INDEX, "cannot choose from an empty sequence");
    }
    return rand_below(n);
}

int64_t ps_rand_range2(int64_t a, int64_t b) {
    int64_t n = b - a + 1;
    if (n <= 0) {
        ps_raise_cstr(PS_VALUE, "empty range for randrange()");
    }
    return a + rand_below(n);
}

int64_t ps_rand_range3(int64_t start, int64_t stop, int64_t step) {
    int64_t width;
    int64_t n;
    if (step == 0) {
        ps_raise_cstr(PS_VALUE, "zero step for randrange()");
    }
    width = stop - start;
    if (step == 1) {
        if (width > 0) {
            return start + rand_below(width);
        }
    } else {
        if (step > 0) {
            n = (width + step - 1) / step;
        } else {
            n = (width + step + 1) / step;
        }
        if (n > 0) {
            return start + step * rand_below(n);
        }
    }
    ps_raise_cstr(PS_VALUE, "empty range for randrange()");
    return 0;
}

double ps_rand_uniform(double a, double b) { return a + (b - a) * ps_rand_f64(); }

void ps_seq_shuffle(PsSeq *s) {
    char *tmp;
    char *base;
    int64_t i;
    if (s->len < 2) {
        return;
    }
    tmp = malloc((size_t)s->elem);
    if (tmp == NULL) {
        abort();
    }
    base = (char *)s->data;
    for (i = s->len - 1; i >= 1; i--) {
        int64_t j = rand_below(i + 1);
        char *a = base + i * s->elem;
        char *b = base + j * s->elem;
        memcpy(tmp, a, (size_t)s->elem);
        memcpy(a, b, (size_t)s->elem);
        memcpy(b, tmp, (size_t)s->elem);
    }
    free(tmp);
}
