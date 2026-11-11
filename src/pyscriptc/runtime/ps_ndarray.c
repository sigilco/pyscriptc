/* The pyscriptc_ndarray runtime (issue #496, M1).
 *
 * An array is a flat, contiguous buffer of fixed-width elements plus the
 * metadata numpy calls a shape. The buffer is the point: the arithmetic
 * below is a C loop over contiguous memory with no per-element box, which
 * is already faster than a CPython list[float] for the arithmetic itself.
 * It is not yet vectorized (M2 rides the IR buffer type, issue #372).
 *
 * This file holds only the arithmetic: the elementwise op loops and the
 * reductions. The buffer representation and everything that touches it
 * (the record, the accessors, the construction and shape entry points)
 * lives in ps_ndbuffer.h, the seam the data-ABI adapters of #63 sit
 * behind (the owner directive of 2026-11-11 on #496).
 *
 * The arithmetic is typed, not double-only: an int32 array adds in int32
 * and wraps like numpy's int32, a float32 array multiplies in float32 and
 * keeps the float32 rounding. The double path is only the float64 case.
 */

#include "ps_ndbuffer.h"

/* ------------------------------------------------------------------ */
/* the elementwise arithmetic                                          */
/* ------------------------------------------------------------------ */

/* The integer arithmetic, done in the array's own width so the int32
   overflow wraps like numpy's int32. */
static int64_t ps_nd_int_op(int64_t op, int64_t a, int64_t b, int64_t width) {
    int64_t r;
    if (width == 4) {
        int32_t x = (int32_t)a;
        int32_t y = (int32_t)b;
        switch (op) {
        case PS_ND_ADD:
            r = (int64_t)(int32_t)(x + y);
            break;
        case PS_ND_SUB:
            r = (int64_t)(int32_t)(x - y);
            break;
        case PS_ND_MUL:
            r = (int64_t)(int32_t)(x * y);
            break;
        case PS_ND_FLOORDIV:
            r = y == 0 ? 0 : (int64_t)(x / y);
            break;
        default:
            r = y == 0 ? 0 : (int64_t)(x % y);
            break;
        }
    } else {
        switch (op) {
        case PS_ND_ADD:
            r = (int64_t)((uint64_t)a + (uint64_t)b);
            break;
        case PS_ND_SUB:
            r = (int64_t)((uint64_t)a - (uint64_t)b);
            break;
        case PS_ND_MUL:
            r = (int64_t)((uint64_t)a * (uint64_t)b);
            break;
        case PS_ND_FLOORDIV:
            r = b == 0 ? 0 : (int64_t)(a / b);
            break;
        default:
            r = b == 0 ? 0 : (int64_t)(a % b);
            break;
        }
    }
    return r;
}

/* The float arithmetic. A float32 array keeps the float32 rounding: the
   operation happens in float and the result is stored back as float. */
static double ps_nd_float_op(int64_t op, double a, double b, int64_t is32) {
    double r;
    switch (op) {
    case PS_ND_ADD:
        r = a + b;
        break;
    case PS_ND_SUB:
        r = a - b;
        break;
    case PS_ND_MUL:
        r = a * b;
        break;
    case PS_ND_DIV:
        r = a / b;
        break;
    case PS_ND_FLOORDIV:
        r = floor(a / b);
        break;
    default:
        r = fmod(a, b);
        break;
    }
    if (is32) {
        r = (double)(float)r;
    }
    return r;
}

/* The elementwise loop over two arrays. The comparison ops produce a bool
   array; the arithmetic ops produce an array of the promoted dtype. */
static PsNd *ps_nd_binop(int64_t op, const PsNd *left, const PsNd *right) {
    int64_t count = left->count;
    int64_t i;
    int is_cmp = op >= PS_ND_EQ;
    int64_t out_dtype;
    int64_t is32;
    PsNd *out;
    if (left->count != right->count) {
        ps_raise_cstr(PS_VALUE, "operands could not be broadcast together");
    }
    if (is_cmp) {
        out_dtype = PS_ND_BOOL;
    } else {
        out_dtype = ps_nd_result_dtype(op, left->dtype, right->dtype);
    }
    is32 = out_dtype == PS_ND_FLOAT32;
    out = ps_nd_alloc(count, ps_nd_width(out_dtype), out_dtype, left->ndim);
    memcpy(out->shape, left->shape, sizeof out->shape);
    for (i = 0; i < count; i++) {
        if (is_cmp) {
            double a = ps_nd_get(left, i);
            double b = ps_nd_get(right, i);
            uint8_t bit;
            switch (op) {
            case PS_ND_EQ:
                bit = a == b ? 1 : 0;
                break;
            case PS_ND_NE:
                bit = a != b ? 1 : 0;
                break;
            case PS_ND_LT:
                bit = a < b ? 1 : 0;
                break;
            case PS_ND_LE:
                bit = a <= b ? 1 : 0;
                break;
            case PS_ND_GT:
                bit = a > b ? 1 : 0;
                break;
            default:
                bit = a >= b ? 1 : 0;
                break;
            }
            ((uint8_t *)out->data)[i] = bit;
            continue;
        }
        if (out_dtype == PS_ND_INT32 || out_dtype == PS_ND_INT64) {
            int64_t width = out_dtype == PS_ND_INT32 ? 4 : 8;
            int64_t a = ps_nd_get_i64(left, i);
            int64_t b = ps_nd_get_i64(right, i);
            ps_nd_set_i64(out, i, ps_nd_int_op(op, a, b, width));
        } else {
            double a = ps_nd_get(left, i);
            double b = ps_nd_get(right, i);
            ps_nd_set(out, i, ps_nd_float_op(op, a, b, is32));
        }
    }
    return out;
}

/* The elementwise loop over an array and a scalar. The `is_float` flag is
   numpy's scalar strength: a Python int is weak (the array's dtype wins),
   a Python float lifts an integer array to float64 but keeps float32.
   The true divide floats in every case. */
static PsNd *ps_nd_binop_scalar(int64_t op, const PsNd *left, double scalar, int64_t is_float) {
    int64_t count = left->count;
    int64_t i;
    int is_cmp = op >= PS_ND_EQ;
    int64_t out_dtype;
    int64_t is32;
    PsNd *out;
    if (is_cmp) {
        out_dtype = PS_ND_BOOL;
    } else {
        out_dtype = ps_nd_result_dtype_scalar(op, left->dtype);
        if (is_float && out_dtype == PS_ND_INT32) {
            /* A Python float with an integer array: the int dtypes lift to
               float64 (numpy: `int32 + 2.5` is float64). */
            out_dtype = PS_ND_FLOAT64;
        }
    }
    is32 = out_dtype == PS_ND_FLOAT32;
    out = ps_nd_alloc(count, ps_nd_width(out_dtype), out_dtype, left->ndim);
    memcpy(out->shape, left->shape, sizeof out->shape);
    for (i = 0; i < count; i++) {
        if (is_cmp) {
            double a = ps_nd_get(left, i);
            uint8_t bit;
            switch (op) {
            case PS_ND_EQ:
                bit = a == scalar ? 1 : 0;
                break;
            case PS_ND_NE:
                bit = a != scalar ? 1 : 0;
                break;
            case PS_ND_LT:
                bit = a < scalar ? 1 : 0;
                break;
            case PS_ND_LE:
                bit = a <= scalar ? 1 : 0;
                break;
            case PS_ND_GT:
                bit = a > scalar ? 1 : 0;
                break;
            default:
                bit = a >= scalar ? 1 : 0;
                break;
            }
            ((uint8_t *)out->data)[i] = bit;
            continue;
        }
        if (out_dtype == PS_ND_INT32 || out_dtype == PS_ND_INT64) {
            int64_t width = out_dtype == PS_ND_INT32 ? 4 : 8;
            int64_t a = ps_nd_get_i64(left, i);
            int64_t b = (int64_t)scalar;
            ps_nd_set_i64(out, i, ps_nd_int_op(op, a, b, width));
        } else {
            double a = ps_nd_get(left, i);
            ps_nd_set(out, i, ps_nd_float_op(op, a, scalar, is32));
        }
    }
    return out;
}

/* The public entry points the emitter calls. The array/array and
   array/scalar cases are separate symbols so the emitter picks one
   without a runtime branch. */
PsNd *ps_nd_add(PsNd *left, PsNd *right) { return ps_nd_binop(PS_ND_ADD, left, right); }
PsNd *ps_nd_sub(PsNd *left, PsNd *right) { return ps_nd_binop(PS_ND_SUB, left, right); }
PsNd *ps_nd_mul(PsNd *left, PsNd *right) { return ps_nd_binop(PS_ND_MUL, left, right); }
PsNd *ps_nd_div(PsNd *left, PsNd *right) { return ps_nd_binop(PS_ND_DIV, left, right); }
PsNd *ps_nd_floordiv(PsNd *left, PsNd *right) { return ps_nd_binop(PS_ND_FLOORDIV, left, right); }
PsNd *ps_nd_mod(PsNd *left, PsNd *right) { return ps_nd_binop(PS_ND_MOD, left, right); }
PsNd *ps_nd_eq(PsNd *left, PsNd *right) { return ps_nd_binop(PS_ND_EQ, left, right); }
PsNd *ps_nd_ne(PsNd *left, PsNd *right) { return ps_nd_binop(PS_ND_NE, left, right); }
PsNd *ps_nd_lt(PsNd *left, PsNd *right) { return ps_nd_binop(PS_ND_LT, left, right); }
PsNd *ps_nd_le(PsNd *left, PsNd *right) { return ps_nd_binop(PS_ND_LE, left, right); }
PsNd *ps_nd_gt(PsNd *left, PsNd *right) { return ps_nd_binop(PS_ND_GT, left, right); }
PsNd *ps_nd_ge(PsNd *left, PsNd *right) { return ps_nd_binop(PS_ND_GE, left, right); }

PsNd *ps_nd_add_s(PsNd *left, double s) { return ps_nd_binop_scalar(PS_ND_ADD, left, s, 0); }
PsNd *ps_nd_sub_s(PsNd *left, double s) { return ps_nd_binop_scalar(PS_ND_SUB, left, s, 0); }
PsNd *ps_nd_mul_s(PsNd *left, double s) { return ps_nd_binop_scalar(PS_ND_MUL, left, s, 0); }
PsNd *ps_nd_div_s(PsNd *left, double s) { return ps_nd_binop_scalar(PS_ND_DIV, left, s, 0); }
PsNd *ps_nd_floordiv_s(PsNd *left, double s) { return ps_nd_binop_scalar(PS_ND_FLOORDIV, left, s, 0); }
PsNd *ps_nd_mod_s(PsNd *left, double s) { return ps_nd_binop_scalar(PS_ND_MOD, left, s, 0); }
PsNd *ps_nd_eq_s(PsNd *left, double s) { return ps_nd_binop_scalar(PS_ND_EQ, left, s, 0); }
PsNd *ps_nd_ne_s(PsNd *left, double s) { return ps_nd_binop_scalar(PS_ND_NE, left, s, 0); }
PsNd *ps_nd_lt_s(PsNd *left, double s) { return ps_nd_binop_scalar(PS_ND_LT, left, s, 0); }
PsNd *ps_nd_le_s(PsNd *left, double s) { return ps_nd_binop_scalar(PS_ND_LE, left, s, 0); }
PsNd *ps_nd_gt_s(PsNd *left, double s) { return ps_nd_binop_scalar(PS_ND_GT, left, s, 0); }
PsNd *ps_nd_ge_s(PsNd *left, double s) { return ps_nd_binop_scalar(PS_ND_GE, left, s, 0); }

/* The scalar paths with a Python float. */
PsNd *ps_nd_add_f(PsNd *left, double s) { return ps_nd_binop_scalar(PS_ND_ADD, left, s, 1); }
PsNd *ps_nd_sub_f(PsNd *left, double s) { return ps_nd_binop_scalar(PS_ND_SUB, left, s, 1); }
PsNd *ps_nd_mul_f(PsNd *left, double s) { return ps_nd_binop_scalar(PS_ND_MUL, left, s, 1); }
PsNd *ps_nd_div_f(PsNd *left, double s) { return ps_nd_binop_scalar(PS_ND_DIV, left, s, 1); }
PsNd *ps_nd_floordiv_f(PsNd *left, double s) { return ps_nd_binop_scalar(PS_ND_FLOORDIV, left, s, 1); }
PsNd *ps_nd_mod_f(PsNd *left, double s) { return ps_nd_binop_scalar(PS_ND_MOD, left, s, 1); }
PsNd *ps_nd_eq_f(PsNd *left, double s) { return ps_nd_binop_scalar(PS_ND_EQ, left, s, 1); }
PsNd *ps_nd_ne_f(PsNd *left, double s) { return ps_nd_binop_scalar(PS_ND_NE, left, s, 1); }
PsNd *ps_nd_lt_f(PsNd *left, double s) { return ps_nd_binop_scalar(PS_ND_LT, left, s, 1); }
PsNd *ps_nd_le_f(PsNd *left, double s) { return ps_nd_binop_scalar(PS_ND_LE, left, s, 1); }
PsNd *ps_nd_gt_f(PsNd *left, double s) { return ps_nd_binop_scalar(PS_ND_GT, left, s, 1); }
PsNd *ps_nd_ge_f(PsNd *left, double s) { return ps_nd_binop_scalar(PS_ND_GE, left, s, 1); }

/* ------------------------------------------------------------------ */
/* the reductions                                                      */
/* ------------------------------------------------------------------ */

/* The reduction dtype: numpy's sum keeps the array's integer dtype
   widened to int64 for the integer dtypes, and keeps float32 for float32. */
int64_t ps_nd_reduce_dtype(int64_t dtype) {
    if (dtype == PS_ND_INT32 || dtype == PS_ND_INT64) {
        return PS_ND_INT64;
    }
    return dtype;
}

/* `sum`: the total over the whole array, or over one axis. The axis path
   accumulates into a fresh array of the reduced shape. */
PsNd *ps_nd_sum(PsNd *a, int64_t axis, int64_t has_axis) {
    int64_t out_dtype = ps_nd_reduce_dtype(a->dtype);
    int64_t is32 = out_dtype == PS_ND_FLOAT32;
    if (!has_axis) {
        int64_t i;
        PsNd *out = ps_nd_alloc(1, ps_nd_width(out_dtype), out_dtype, 0);
        if (out_dtype == PS_ND_INT64) {
            int64_t total = 0;
            for (i = 0; i < a->count; i++) {
                total = (int64_t)((uint64_t)total + (uint64_t)ps_nd_get_i64(a, i));
            }
            ((int64_t *)out->data)[0] = total;
        } else {
            double total = 0.0;
            for (i = 0; i < a->count; i++) {
                total += ps_nd_get(a, i);
            }
            if (is32) {
                total = (double)(float)total;
            }
            if (out_dtype == PS_ND_FLOAT32) {
                ((float *)out->data)[0] = (float)total;
            } else {
                ((double *)out->data)[0] = total;
            }
        }
        return out;
    }
    if (axis < 0 || axis >= a->ndim) {
        ps_raise_cstr(PS_VALUE, "axis out of bounds");
    }
    {
        int64_t outer = 1;
        int64_t inner = 1;
        int64_t i;
        int64_t j;
        int64_t k;
        int64_t dim = a->shape[axis];
        PsNd *out;
        for (i = 0; i < axis; i++) {
            outer *= a->shape[i];
        }
        for (i = axis + 1; i < a->ndim; i++) {
            inner *= a->shape[i];
        }
        out = ps_nd_alloc(outer * inner, ps_nd_width(out_dtype), out_dtype, a->ndim - 1);
        for (i = 0; i < a->ndim; i++) {
            if (i < axis) {
                out->shape[i] = a->shape[i];
            } else if (i > axis) {
                out->shape[i - 1] = a->shape[i];
            }
        }
        for (i = 0; i < outer; i++) {
            for (j = 0; j < inner; j++) {
                if (out_dtype == PS_ND_INT64) {
                    int64_t total = 0;
                    for (k = 0; k < dim; k++) {
                        total = (int64_t)((uint64_t)total + (uint64_t)ps_nd_get_i64(a, (i * dim + k) * inner + j));
                    }
                    ((int64_t *)out->data)[i * inner + j] = total;
                } else {
                    double total = 0.0;
                    for (k = 0; k < dim; k++) {
                        total += ps_nd_get(a, (i * dim + k) * inner + j);
                    }
                    if (is32) {
                        total = (double)(float)total;
                    }
                    if (out_dtype == PS_ND_FLOAT32) {
                        ((float *)out->data)[i * inner + j] = (float)total;
                    } else {
                        ((double *)out->data)[i * inner + j] = total;
                    }
                }
            }
        }
        return out;
    }
}

/* `min` / `max`: the extreme over the whole array, or over one axis. The
   dtype is the array's own. */
PsNd *ps_nd_extreme(PsNd *a, int64_t axis, int64_t has_axis, int want_max) {
    if (!has_axis) {
        double best;
        int64_t i;
        PsNd *out = ps_nd_alloc(1, ps_nd_width(a->dtype), a->dtype, 0);
        if (a->count == 0) {
            ps_raise_cstr(PS_VALUE, "zero-size array reduction");
        }
        best = ps_nd_get(a, 0);
        for (i = 1; i < a->count; i++) {
            double v = ps_nd_get(a, i);
            if (want_max ? (v > best) : (v < best)) {
                best = v;
            }
        }
        ps_nd_set(out, 0, best);
        return out;
    }
    if (axis < 0 || axis >= a->ndim) {
        ps_raise_cstr(PS_VALUE, "axis out of bounds");
    }
    {
        int64_t outer = 1;
        int64_t inner = 1;
        int64_t i;
        int64_t j;
        int64_t k;
        int64_t dim = a->shape[axis];
        PsNd *out;
        for (i = 0; i < axis; i++) {
            outer *= a->shape[i];
        }
        for (i = axis + 1; i < a->ndim; i++) {
            inner *= a->shape[i];
        }
        out = ps_nd_alloc(outer * inner, ps_nd_width(a->dtype), a->dtype, a->ndim - 1);
        for (i = 0; i < a->ndim; i++) {
            if (i < axis) {
                out->shape[i] = a->shape[i];
            } else if (i > axis) {
                out->shape[i - 1] = a->shape[i];
            }
        }
        for (i = 0; i < outer; i++) {
            for (j = 0; j < inner; j++) {
                double best = ps_nd_get(a, (i * dim) * inner + j);
                for (k = 1; k < dim; k++) {
                    double v = ps_nd_get(a, (i * dim + k) * inner + j);
                    if (want_max ? (v > best) : (v < best)) {
                        best = v;
                    }
                }
                ps_nd_set(out, i * inner + j, best);
            }
        }
        return out;
    }
}

PsNd *ps_nd_min(PsNd *a, int64_t axis, int64_t has_axis) { return ps_nd_extreme(a, axis, has_axis, 0); }
PsNd *ps_nd_max(PsNd *a, int64_t axis, int64_t has_axis) { return ps_nd_extreme(a, axis, has_axis, 1); }

/* `mean`: the arithmetic mean over the whole array, or over one axis. The
   dtype is float64 for the integer dtypes and the array's own for the
   float dtypes. */
PsNd *ps_nd_mean(PsNd *a, int64_t axis, int64_t has_axis) {
    int64_t out_dtype = (a->dtype == PS_ND_FLOAT32 || a->dtype == PS_ND_FLOAT64) ? a->dtype : PS_ND_FLOAT64;
    if (!has_axis) {
        double total = 0.0;
        int64_t i;
        PsNd *out;
        if (a->count == 0) {
            ps_raise_cstr(PS_VALUE, "zero-size array reduction");
        }
        for (i = 0; i < a->count; i++) {
            total += ps_nd_get(a, i);
        }
        out = ps_nd_alloc(1, ps_nd_width(out_dtype), out_dtype, 0);
        if (out_dtype == PS_ND_FLOAT32) {
            ((float *)out->data)[0] = (float)(total / (double)a->count);
        } else {
            ((double *)out->data)[0] = total / (double)a->count;
        }
        return out;
    }
    {
        /* The per-axis mean: the sum over that axis, divided by its
           extent. The sum keeps the reduction dtype, so the division
           happens in the output dtype. */
        int64_t outer = 1;
        int64_t inner = 1;
        int64_t i;
        int64_t j;
        int64_t k;
        int64_t dim = a->shape[axis];
        PsNd *out;
        for (i = 0; i < axis; i++) {
            outer *= a->shape[i];
        }
        for (i = axis + 1; i < a->ndim; i++) {
            inner *= a->shape[i];
        }
        out = ps_nd_alloc(outer * inner, ps_nd_width(out_dtype), out_dtype, a->ndim - 1);
        for (i = 0; i < a->ndim; i++) {
            if (i < axis) {
                out->shape[i] = a->shape[i];
            } else if (i > axis) {
                out->shape[i - 1] = a->shape[i];
            }
        }
        for (i = 0; i < outer; i++) {
            for (j = 0; j < inner; j++) {
                double total = 0.0;
                for (k = 0; k < dim; k++) {
                    total += ps_nd_get(a, (i * dim + k) * inner + j);
                }
                if (out_dtype == PS_ND_FLOAT32) {
                    ((float *)out->data)[i * inner + j] = (float)(total / (double)dim);
                } else {
                    ((double *)out->data)[i * inner + j] = total / (double)dim;
                }
            }
        }
        return out;
    }
}

/* `var` and `std`: the population variance (numpy defaults to ddof=0).
   The dtype follows `mean`: float64 for the integer dtypes, the array's
   own for the float dtypes. */
PsNd *ps_nd_var(PsNd *a, int64_t axis, int64_t has_axis) {
    int64_t out_dtype = (a->dtype == PS_ND_FLOAT32 || a->dtype == PS_ND_FLOAT64) ? a->dtype : PS_ND_FLOAT64;
    if (!has_axis) {
        double mean;
        double total = 0.0;
        int64_t i;
        PsNd *out;
        if (a->count == 0) {
            ps_raise_cstr(PS_VALUE, "zero-size array reduction");
        }
        {
            double sum = 0.0;
            for (i = 0; i < a->count; i++) {
                sum += ps_nd_get(a, i);
            }
            mean = sum / (double)a->count;
        }
        for (i = 0; i < a->count; i++) {
            double d = ps_nd_get(a, i) - mean;
            total += d * d;
        }
        out = ps_nd_alloc(1, ps_nd_width(out_dtype), out_dtype, 0);
        if (out_dtype == PS_ND_FLOAT32) {
            ((float *)out->data)[0] = (float)(total / (double)a->count);
        } else {
            ((double *)out->data)[0] = total / (double)a->count;
        }
        return out;
    }
    {
        /* The per-axis variance: the mean per column, then the squared
           deviations of that column. */
        int64_t outer = 1;
        int64_t inner = 1;
        int64_t i;
        int64_t j;
        int64_t k;
        int64_t dim = a->shape[axis];
        PsNd *out;
        for (i = 0; i < axis; i++) {
            outer *= a->shape[i];
        }
        for (i = axis + 1; i < a->ndim; i++) {
            inner *= a->shape[i];
        }
        out = ps_nd_alloc(outer * inner, ps_nd_width(out_dtype), out_dtype, a->ndim - 1);
        for (i = 0; i < a->ndim; i++) {
            if (i < axis) {
                out->shape[i] = a->shape[i];
            } else if (i > axis) {
                out->shape[i - 1] = a->shape[i];
            }
        }
        for (i = 0; i < outer; i++) {
            for (j = 0; j < inner; j++) {
                double sum = 0.0;
                double total = 0.0;
                for (k = 0; k < dim; k++) {
                    sum += ps_nd_get(a, (i * dim + k) * inner + j);
                }
                sum /= (double)dim;
                for (k = 0; k < dim; k++) {
                    double d = ps_nd_get(a, (i * dim + k) * inner + j) - sum;
                    total += d * d;
                }
                if (out_dtype == PS_ND_FLOAT32) {
                    ((float *)out->data)[i * inner + j] = (float)(total / (double)dim);
                } else {
                    ((double *)out->data)[i * inner + j] = total / (double)dim;
                }
            }
        }
        return out;
    }
}

/* `std`: the square root of the variance. */
PsNd *ps_nd_std(PsNd *a, int64_t axis, int64_t has_axis) {
    PsNd *v = ps_nd_var(a, axis, has_axis);
    int64_t i;
    for (i = 0; i < v->count; i++) {
        double d = ps_nd_get(v, i);
        ps_nd_set(v, i, sqrt(d < 0.0 ? 0.0 : d));
    }
    return v;
}

/* `argmin` / `argmax`: the index of the extreme, as a flat index over the
   whole array. The axis form is not part of the M1 subset. */
PsNd *ps_nd_argmin(PsNd *a) {
    int64_t best = 0;
    int64_t i;
    PsNd *out;
    if (a->count == 0) {
        ps_raise_cstr(PS_VALUE, "attempt to get argmin of an empty sequence");
    }
    for (i = 1; i < a->count; i++) {
        if (ps_nd_get(a, i) < ps_nd_get(a, best)) {
            best = i;
        }
    }
    out = ps_nd_alloc(1, 8, PS_ND_INT64, 0);
    ((int64_t *)out->data)[0] = best;
    return out;
}

PsNd *ps_nd_argmax(PsNd *a) {
    int64_t best = 0;
    int64_t i;
    PsNd *out;
    if (a->count == 0) {
        ps_raise_cstr(PS_VALUE, "attempt to get argmax of an empty sequence");
    }
    for (i = 1; i < a->count; i++) {
        if (ps_nd_get(a, i) > ps_nd_get(a, best)) {
            best = i;
        }
    }
    out = ps_nd_alloc(1, 8, PS_ND_INT64, 0);
    ((int64_t *)out->data)[0] = best;
    return out;
}
