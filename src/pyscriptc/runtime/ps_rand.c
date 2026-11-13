/* random: one shared MT19937 instance, seeded the way CPython's
   _randommodule seeds it — init_by_array over the int's little-endian
   32-bit limbs — so an int seed gives a CPython-identical stream.
   seed() with no argument is nondeterministic (time/clock/address mix). */
#include "ps_rt.h"

#include <pthread.h>
#include <math.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#define MT_N 624
#define MT_M 397

static uint32_t mt[MT_N];
static int mt_idx = MT_N;
static int mt_seeded = 0;
/* The cached second normal of gauss(): reset by every seed, as CPython does. */
static double gauss_next = 0.0;
static int gauss_has_next = 0;
/* CPython's module-level random is one shared instance, so its sequence is
   shared across threads. One mutex keeps the state coherent; interleaved
   draws stay ordered, which is all CPython promises. */
static pthread_mutex_t ps_rand_lock = PTHREAD_MUTEX_INITIALIZER;

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

/* The unlocked seeds: the caller holds ps_rand_lock. */
static void ps_rand_seed_auto_locked(void) {
    uint64_t x = (uint64_t)time(NULL);
    uint32_t key[2];
    x = x * 0x9e3779b97f4a7c15ULL + 0x165667b19e3779f9ULL;
    x ^= (uint64_t)clock() << 32 | (uint64_t)clock();
    x ^= (uint64_t)(uintptr_t)&mt[0];
    key[0] = (uint32_t)(x & 0xffffffffU);
    key[1] = (uint32_t)(x >> 32);
    mt_init_array(key, 2);
    mt_seeded = 1;
    gauss_has_next = 0;
}

static void ps_rand_seed_locked(int64_t v) {
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
    gauss_has_next = 0;
}

/* The public seeds: the lowerer calls these, so they take the lock. */
void ps_rand_seed_auto(void) {
    pthread_mutex_lock(&ps_rand_lock);
    ps_rand_seed_auto_locked();
    pthread_mutex_unlock(&ps_rand_lock);
}

void ps_rand_seed(int64_t v) {
    pthread_mutex_lock(&ps_rand_lock);
    ps_rand_seed_locked(v);
    pthread_mutex_unlock(&ps_rand_lock);
}

int64_t ps_rand_bits(int64_t k) {
    int64_t out;
    if (k < 0 || k > 64) {
        ps_raise_cstr(PS_VALUE, "number of bits must be 0 to 64");
    }
    if (k == 0) {
        return 0;
    }
    pthread_mutex_lock(&ps_rand_lock);
    if (!mt_seeded) {
        ps_rand_seed_auto_locked();
    }
    if (k <= 32) {
        out = (int64_t)(mt_next() >> (32 - k));
    } else {
        uint64_t lo = mt_next();
        uint64_t hi = mt_next() >> (64 - k);
        out = (int64_t)((hi << 32) | lo);
    }
    pthread_mutex_unlock(&ps_rand_lock);
    return out;
}

double ps_rand_f64(void) {
    uint32_t a;
    uint32_t b;
    pthread_mutex_lock(&ps_rand_lock);
    if (!mt_seeded) {
        ps_rand_seed_auto_locked();
    }
    a = mt_next() >> 5;
    b = mt_next() >> 6;
    pthread_mutex_unlock(&ps_rand_lock);
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

/* ---- randbytes: getrandbits(8 * n).to_bytes(n, "little") --------------- */

void ps_rand_bytes(int64_t n, PsBuf *out) {
    char *q;
    int64_t i;
    if (n < 0) {
        ps_raise_cstr(PS_VALUE, "number of bits must be non-negative");
    }
    q = malloc((size_t)n + 1);
    if (q == NULL) {
        abort();
    }
    pthread_mutex_lock(&ps_rand_lock);
    if (!mt_seeded) {
        ps_rand_seed_auto_locked();
    }
    for (i = 0; i < n; i += 4) {
        int64_t take = n - i < 4 ? n - i : 4;
        uint32_t r = mt_next();
        int64_t b;
        if (take < 4) {
            r >>= 32 - 8 * take;
        }
        for (b = 0; b < take; b++) {
            q[i + b] = (char)((r >> (8 * b)) & 0xffU);
        }
    }
    pthread_mutex_unlock(&ps_rand_lock);
    q[n] = '\0';
    out->len = n;
    out->data = q;
}

/* ---- choices and sample ------------------------------------------------ */

static void seq_copy_elem(PsSeq *dst, int64_t di, PsSeq *src, int64_t si) {
    memcpy((char *)dst->data + di * dst->elem, (char *)src->data + si * src->elem, (size_t)src->elem);
}

static PsSeq *seq_empty_like(PsSeq *pop, int64_t n) {
    PsSeq *out = ps_seq_new(pop->elem, n);
    return out;
}

/* choices(pop, k=k): floor(random() * n) per draw. */
PsSeq *ps_rand_choices(PsSeq *pop, int64_t k) {
    int64_t n = pop->len;
    int64_t i;
    PsSeq *out;
    if (k < 0) {
        k = 0;
    }
    out = seq_empty_like(pop, k);
    for (i = 0; i < k; i++) {
        int64_t j = (int64_t)floor(ps_rand_f64() * ((double)n + 0.0));
        if (j < 0 || j >= n) {
            ps_raise_cstr(PS_INDEX, "list index out of range");
        }
        seq_copy_elem(out, i, pop, j);
    }
    return out;
}

/* choices(pop, weights=w, k=k): bisect_right over the cumulative weights.
   is_float selects the weight cell type (int64 or double). */
PsSeq *ps_rand_choices_w(PsSeq *pop, PsSeq *weights, int64_t k, int64_t is_float) {
    int64_t n = pop->len;
    int64_t i;
    int64_t hi;
    double total;
    double *cum;
    PsSeq *out;
    if (weights->len != n) {
        ps_raise_cstr(PS_VALUE, "The number of weights does not match the population");
    }
    if (n == 0) {
        ps_raise_cstr(PS_INDEX, "list index out of range");
    }
    cum = malloc((size_t)n * sizeof(double));
    if (cum == NULL) {
        abort();
    }
    if (is_float) {
        double acc = 0.0;
        for (i = 0; i < n; i++) {
            acc = i == 0 ? ((double *)weights->data)[0] : acc + ((double *)weights->data)[i];
            cum[i] = acc;
        }
    } else {
        int64_t acc = 0;
        for (i = 0; i < n; i++) {
            acc += ((int64_t *)weights->data)[i];
            cum[i] = (double)acc;
        }
    }
    total = cum[n - 1] + 0.0;
    if (total <= 0.0) {
        free(cum);
        ps_raise_cstr(PS_VALUE, "Total of weights must be greater than zero");
    }
    if (!isfinite(total)) {
        free(cum);
        ps_raise_cstr(PS_VALUE, "Total of weights must be finite");
    }
    if (k < 0) {
        k = 0;
    }
    hi = n - 1;
    out = seq_empty_like(pop, k);
    for (i = 0; i < k; i++) {
        double x = ps_rand_f64() * total;
        int64_t lo = 0;
        int64_t h = hi;
        while (lo < h) {
            int64_t mid = (lo + h) / 2;
            if (x < cum[mid]) {
                h = mid;
            } else {
                lo = mid + 1;
            }
        }
        seq_copy_elem(out, i, pop, lo);
    }
    free(cum);
    return out;
}

/* sample(pop, k): CPython's pool branch for small populations and the
   selected-set branch otherwise, so a seeded run draws the same indexes. */
PsSeq *ps_rand_sample(PsSeq *pop, int64_t k) {
    int64_t n = pop->len;
    int64_t i;
    int64_t setsize = 21;
    PsSeq *out;
    if (k < 0 || k > n) {
        ps_raise_cstr(PS_VALUE, "Sample larger than population or is negative");
    }
    if (k > 5) {
        setsize += (int64_t)pow(4.0, ceil(log((double)(k * 3)) / log(4.0)));
    }
    out = seq_empty_like(pop, k);
    if (n <= setsize) {
        int64_t *pool = malloc((size_t)(n > 0 ? n : 1) * sizeof(int64_t));
        if (pool == NULL) {
            abort();
        }
        for (i = 0; i < n; i++) {
            pool[i] = i;
        }
        for (i = 0; i < k; i++) {
            int64_t j = rand_below(n - i);
            seq_copy_elem(out, i, pop, pool[j]);
            pool[j] = pool[n - i - 1];
        }
        free(pool);
    } else {
        unsigned char *selected = calloc((size_t)n, 1);
        if (selected == NULL) {
            abort();
        }
        for (i = 0; i < k; i++) {
            int64_t j = rand_below(n);
            while (selected[j]) {
                j = rand_below(n);
            }
            selected[j] = 1;
            seq_copy_elem(out, i, pop, j);
        }
        free(selected);
    }
    return out;
}

/* ---- the distribution draws (CPython 3.14 random.py, same libm calls) -- */

#define PS_TWOPI 6.283185307179586
#define PS_LOG4 1.3862943611198906
#define PS_NV_MAGICCONST 1.7155277699214135
#define PS_SG_MAGICCONST 2.504077396776274

double ps_rand_normalvariate(double mu, double sigma) {
    double z;
    for (;;) {
        double u1 = ps_rand_f64();
        double u2 = 1.0 - ps_rand_f64();
        double zz;
        z = PS_NV_MAGICCONST * (u1 - 0.5) / u2;
        zz = z * z / 4.0;
        if (zz <= -log(u2)) {
            break;
        }
    }
    return mu + z * sigma;
}

double ps_rand_gauss(double mu, double sigma) {
    double z;
    int have;
    pthread_mutex_lock(&ps_rand_lock);
    have = gauss_has_next;
    z = gauss_next;
    gauss_has_next = 0;
    pthread_mutex_unlock(&ps_rand_lock);
    if (!have) {
        double x2pi = ps_rand_f64() * PS_TWOPI;
        double g2rad = sqrt(-2.0 * log(1.0 - ps_rand_f64()));
        z = cos(x2pi) * g2rad;
        pthread_mutex_lock(&ps_rand_lock);
        gauss_next = sin(x2pi) * g2rad;
        gauss_has_next = 1;
        pthread_mutex_unlock(&ps_rand_lock);
    }
    return mu + z * sigma;
}

double ps_rand_lognormvariate(double mu, double sigma) { return exp(ps_rand_normalvariate(mu, sigma)); }

double ps_rand_expovariate(double lambd) {
    double u = ps_rand_f64();
    if (lambd == 0.0) {
        ps_raise_cstr(PS_ZERO_DIV, "division by zero");
    }
    return -log(1.0 - u) / lambd;
}

double ps_rand_triangular(double low, double high, double mode, int64_t has_mode) {
    double u = ps_rand_f64();
    double c = 0.5;
    if (has_mode) {
        if (high - low == 0.0) {
            return low;
        }
        c = (mode - low) / (high - low);
    }
    if (u > c) {
        double t = low;
        u = 1.0 - u;
        c = 1.0 - c;
        low = high;
        high = t;
    }
    return low + (high - low) * sqrt(u * c);
}

double ps_rand_gammavariate(double alpha, double beta) {
    if (alpha <= 0.0 || beta <= 0.0) {
        ps_raise_cstr(PS_VALUE, "gammavariate: alpha and beta must be > 0.0");
    }
    if (alpha > 1.0) {
        double ainv = sqrt(2.0 * alpha - 1.0);
        double bbb = alpha - PS_LOG4;
        double ccc = alpha + ainv;
        for (;;) {
            double u1 = ps_rand_f64();
            double u2;
            double v;
            double x;
            double z;
            double r;
            if (!(1e-7 < u1 && u1 < 0.9999999)) {
                continue;
            }
            u2 = 1.0 - ps_rand_f64();
            v = log(u1 / (1.0 - u1)) / ainv;
            x = alpha * exp(v);
            z = u1 * u1 * u2;
            r = bbb + ccc * v - x;
            if (r + PS_SG_MAGICCONST - 4.5 * z >= 0.0 || r >= log(z)) {
                return x * beta;
            }
        }
    } else if (alpha == 1.0) {
        return -log(1.0 - ps_rand_f64()) * beta;
    } else {
        double x;
        for (;;) {
            double u = ps_rand_f64();
            double b = (2.718281828459045 + alpha) / 2.718281828459045;
            double p = b * u;
            double u1;
            if (p <= 1.0) {
                x = pow(p, 1.0 / alpha);
            } else {
                x = -log((b - p) / alpha);
            }
            u1 = ps_rand_f64();
            if (p > 1.0) {
                if (u1 <= pow(x, alpha - 1.0)) {
                    break;
                }
            } else if (u1 <= exp(-x)) {
                break;
            }
        }
        return x * beta;
    }
}

double ps_rand_betavariate(double alpha, double beta) {
    double y = ps_rand_gammavariate(alpha, 1.0);
    if (y != 0.0) {
        return y / (y + ps_rand_gammavariate(beta, 1.0));
    }
    return 0.0;
}
