/* SPDX-License-Identifier: Apache-2.0 */
/* The pyscriptc_frame runtime (issue #497, slice 1).
 *
 * A frame is a dict of named columns, each column a flat typed buffer
 * (a PsNd from ps_ndbuffer.h), plus the row count the columns share and
 * the schema the compiler carries on the IR type's label. The column map
 * is a PsMap keyed by str (insertion order, divergence 6), so the
 * iteration order of `df.columns` is the construction order, the order
 * pandas keeps.
 *
 * Every frame value is the pointer to this record; every read goes
 * through one of the entry points below. The column dtypes ride the
 * compiler's IR type (`DataFrame[a=float64,b=int64]`), so the compiler
 * never guesses a dtype here and a name outside the schema fences at
 * compile time; the checks here are runtime backstops for that contract.
 *
 * Slice 1 surface: construction from per-column arrays, the column
 * reads and writes, the metadata (columns, shape, len), head, tail and
 * the homogeneous to_dict.
 */

#include "ps_rt.h"

#include <stddef.h>
#include <string.h>

/* The ndarray entry points ps_frame.c reaches, declared here rather than
   through ps_ndbuffer.h: that header defines the buffer functions
   in-header, so including it would put a second copy of every ps_nd_*
   symbol in this object and the link would see two definitions. The
   record layout the externs spell is the one ps_ndbuffer.h owns. */

typedef struct PsNd {
    void *data;
    int64_t count;
    int64_t width;
    int64_t dtype;
    int64_t ndim;
    int64_t shape[4];
    int64_t owns;
} PsNd;

enum {
    PS_ND_INT32 = 0,
    PS_ND_INT64 = 1,
    PS_ND_FLOAT32 = 2,
    PS_ND_FLOAT64 = 3,
    PS_ND_BOOL = 4
};

extern PsNd *ps_nd_alloc(int64_t count, int64_t width, int64_t dtype, int64_t ndim);
extern PsNd *ps_nd_from_seq(int64_t dtype, PsSeq *data, int64_t is_int);
extern double ps_nd_get(const PsNd *a, int64_t i);
extern int64_t ps_nd_get_i64(const PsNd *a, int64_t i);

/* The record. One allocation per frame; the columns are the PsNd
   pointers the ndarray runtime already owns. */
typedef struct PsFrame {
    PsMap *cols;  /* str -> PsNd*, insertion order = column order */
    int64_t rows; /* the row count every column shares */
} PsFrame;

/* ------------------------------------------------------------------ */
/* the record helpers                                                  */
/* ------------------------------------------------------------------ */

static PsFrame *ps_frame_alloc(int64_t rows) {
    PsFrame *f = (PsFrame *)ps_alloc((int64_t)sizeof(PsFrame));
    f->cols = ps_map_new(2, (int64_t)sizeof(PsBuf), (int64_t)sizeof(PsNd *), 4);
    f->rows = rows;
    return f;
}

static void ps_frame_check_row(const PsFrame *f, int64_t n) {
    if (n != f->rows) {
        ps_raise_cstr(PS_VALUE, "columns have different lengths");
    }
}

/* ------------------------------------------------------------------ */
/* construction                                                        */
/* ------------------------------------------------------------------ */

/* `DataFrame({...})`: the lowering hands one `with_col` call per column
   onto an empty frame; this is the empty start. */
PsFrame *ps_frame_new(void) {
    return ps_frame_alloc(0);
}

/* One column of the construction dict: the name and the column array.
   The first column fixes the row count; every later column is checked
   against it. The column order is the call order, which is the display
   order. */
PsFrame *ps_frame_with_col(PsFrame *f, int64_t name_len, const char *name_data, PsNd *col) {
    PsBuf key;
    if (f->rows == 0 && ps_map_len(f->cols) == 0) {
        f->rows = col->count;
    }
    ps_frame_check_row(f, col->count);
    ps_buf_copy(&key, name_data, name_len);
    ps_map_put(f->cols, &key, &col);
    return f;
}

/* ------------------------------------------------------------------ */
/* the metadata                                                        */
/* ------------------------------------------------------------------ */

int64_t ps_frame_len(const PsFrame *f) {
    return f->rows;
}

/* `df.columns`: the names in insertion order, as a list[str]. The map's
   key cells are the 16-byte PsBuf prefix the emitter's str-list cells
   hold, so `ps_dict_keys` is the list. */
PsSeq *ps_frame_columns(const PsFrame *f) {
    return ps_dict_keys(f->cols);
}

int64_t ps_frame_ncols(const PsFrame *f) {
    return ps_map_len(f->cols);
}

/* ------------------------------------------------------------------ */
/* the column access                                                   */
/* ------------------------------------------------------------------ */

static int64_t ps_frame_col_index(const PsFrame *f, int64_t name_len, const char *name_data) {
    int64_t n = ps_map_len(f->cols);
    int64_t i;
    for (i = 0; i < n; i++) {
        PsBuf at;
        ps_map_key_at(f->cols, i, &at);
        if (at.len == name_len && memcmp(at.data, name_data, (size_t)name_len) == 0) {
            return i;
        }
    }
    return -1;
}

static PsNd *ps_frame_col_ptr(const PsFrame *f, int64_t i) {
    PsNd *col = NULL;
    ps_map_val_at(f->cols, i, &col);
    return col;
}

/* `df["col"]`: the column as the ndarray the runtime already owns. The
   compiler checked the name against the schema; the raise is the
   backstop. */
PsNd *ps_frame_getcol(const PsFrame *f, int64_t name_len, const char *name_data) {
    int64_t i = ps_frame_col_index(f, name_len, name_data);
    if (i < 0) {
        ps_raise_cstr(PS_INDEX, "column not found");
    }
    return ps_frame_col_ptr(f, i);
}

/* `df["col"] = values`: replace an existing column of the same dtype.
   Slice 1 keeps the schema fixed: a new name or a different dtype is a
   compile-time fence, the checks here are the backstops. */
void ps_frame_setcol(PsFrame *f, int64_t name_len, const char *name_data, PsNd *col) {
    int64_t i = ps_frame_col_index(f, name_len, name_data);
    PsBuf key;
    if (i < 0) {
        ps_raise_cstr(PS_INDEX, "column not found");
    }
    ps_frame_check_row(f, col->count);
    if (ps_frame_col_ptr(f, i)->dtype != col->dtype) {
        ps_raise_cstr(PS_VALUE, "column dtype does not match the frame's schema");
    }
    ps_buf_copy(&key, name_data, name_len);
    ps_map_put(f->cols, &key, &col);
}

/* ------------------------------------------------------------------ */
/* head, tail                                                          */
/* ------------------------------------------------------------------ */

static PsFrame *ps_frame_slice_rows(const PsFrame *f, int64_t start, int64_t stop) {
    PsFrame *out;
    int64_t n = ps_map_len(f->cols);
    int64_t i;
    if (start < 0) {
        start = 0;
    }
    if (stop > f->rows) {
        stop = f->rows;
    }
    if (stop < start) {
        stop = start;
    }
    out = ps_frame_alloc(stop - start);
    for (i = 0; i < n; i++) {
        PsBuf key;
        PsNd *col = ps_frame_col_ptr(f, i);
        PsNd *slice;
        int64_t j;
        ps_map_key_at(f->cols, i, &key);
        slice = ps_nd_alloc(out->rows, col->width, col->dtype, 1);
        slice->shape[0] = out->rows;
        for (j = 0; j < out->rows; j++) {
            memcpy((char *)slice->data + (size_t)j * (size_t)col->width,
                   (char *)col->data + (size_t)(start + j) * (size_t)col->width,
                   (size_t)col->width);
        }
        ps_map_put(out->cols, &key, &slice);
    }
    return out;
}

/* `df.head(n)`: the first n rows (pandas' default n=5 rides the
   lowering's constant). */
PsFrame *ps_frame_head(const PsFrame *f, int64_t n) {
    if (n < 0) {
        ps_raise_cstr(PS_VALUE, "head count is negative");
    }
    return ps_frame_slice_rows(f, 0, n);
}

/* `df.tail(n)`: the last n rows. */
PsFrame *ps_frame_tail(const PsFrame *f, int64_t n) {
    int64_t start;
    if (n < 0) {
        ps_raise_cstr(PS_VALUE, "tail count is negative");
    }
    start = f->rows - n;
    if (start < 0) {
        start = 0;
    }
    return ps_frame_slice_rows(f, start, f->rows);
}

/* ------------------------------------------------------------------ */
/* to_dict                                                             */
/* ------------------------------------------------------------------ */

/* `df.to_dict()` (the default orient): a dict[str, list[number]]. The
   frame must be homogeneous — every column the same dtype — while the
   IR's dict value type is closed; the compiler checked that on the
   schema label, the check here is the backstop. The list cells hold the
   column's values in the shape its element type wants, the same packing
   ps_nd_tolist does for a flat column. */
PsMap *ps_frame_to_dict(const PsFrame *f) {
    PsMap *out = ps_map_new(2, (int64_t)sizeof(PsBuf), (int64_t)sizeof(PsSeq *), 4);
    int64_t n = ps_map_len(f->cols);
    int64_t i;
    int64_t dtype = -1;
    for (i = 0; i < n; i++) {
        PsBuf key;
        PsSeq *vals;
        PsNd *col = ps_frame_col_ptr(f, i);
        int64_t j;
        if (dtype < 0) {
            dtype = col->dtype;
        } else if (dtype != col->dtype) {
            ps_raise_cstr(PS_VALUE, "to_dict needs a homogeneous frame");
        }
        vals = ps_seq_new(8, 0);
        for (j = 0; j < col->count; j++) {
            if (col->dtype == PS_ND_INT32 || col->dtype == PS_ND_INT64) {
                int64_t v = ps_nd_get_i64(col, j);
                ps_seq_push(vals, &v);
            } else {
                double v = ps_nd_get(col, j);
                ps_seq_push(vals, &v);
            }
        }
        ps_map_key_at(f->cols, i, &key);
        ps_map_put(out, &key, &vals);
    }
    return out;
}
