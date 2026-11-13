/* pyscriptc-arrow thunk (issue #1209).

The manifest binds `abi = ps_ffi_arrow_*` here, never the nanoarrow symbols.
This file owns everything the ABI cannot express: the Arrow C Data struct
pair lifetime, the dtype word table, the list marshalling (the subset list
is a PsSeq of 8-byte slots), the Arrow IPC wire form and the error mapping.

The vendored nanoarrow is the namespaced bundle (PsArrow), so the symbols
here are the only Arrow symbols in the binary.

Strings arrive as (i64 len, ptr) pairs and are not NUL terminated.

Exception surface (mirrored in pyscriptc_arrow.pyi):
  PS_VALUEERROR    the dtype word is unknown, a value is out of range, or a
                   handle was already released
  PS_TYPEERROR     the values list holds an element type the dtype refuses
  PS_RUNTIMEERROR  nanoarrow refused an operation; "arrow: <text>"
  PS_INDEXERROR    an out-of-range subscript

The element slot of a list is 8 bytes wide (the runtime's `_elem_size`), so
an int or a float rides the same slot; the dtype word says how to read it.
A utf8 element is a PsBuf prefix (16 bytes), which the string dtype reads
directly. */

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "nanoarrow/nanoarrow.h"
#include "ps_rt.h"

/* The dtype words the manifest's constants carry. They are the adapter's
   own numbering, not nanoarrow's enum, so the wire vocabulary does not
   drift when nanoarrow reorders its enum. */
#define PS_ARROW_INT32 1
#define PS_ARROW_INT64 2
#define PS_ARROW_FLOAT32 3
#define PS_ARROW_FLOAT64 4
#define PS_ARROW_BOOL 5
#define PS_ARROW_UTF8 6

/* The Arrow C Data struct pair a handle owns. The wrapper is what makes a
   released handle detectable: `released` is set once the pair is gone, so a
   stale handle raises instead of touching freed memory (the sqlite pilot's
   wrapper discipline). */
typedef struct ps_arrow_array {
    struct ArrowArray array;
    struct ArrowSchema schema;
    int64_t dtype;
    int released;
} ps_arrow_array;

static void ps_arrow_fail_nanoarrow(const char *what, const struct ArrowError *error) {
    char msg[512];
    snprintf(msg, sizeof msg, "arrow: %s: %.400s", what, error != NULL ? error->message : "unknown error");
    ps_raise_cstr(PS_RUNTIMEERROR, msg);
}

/* The dtype word to the nanoarrow type. */
static enum ArrowType ps_arrow_type(int64_t dtype) {
    switch (dtype) {
        case PS_ARROW_INT32:
            return NANOARROW_TYPE_INT32;
        case PS_ARROW_INT64:
            return NANOARROW_TYPE_INT64;
        case PS_ARROW_FLOAT32:
            return NANOARROW_TYPE_FLOAT;
        case PS_ARROW_FLOAT64:
            return NANOARROW_TYPE_DOUBLE;
        case PS_ARROW_BOOL:
            return NANOARROW_TYPE_BOOL;
        case PS_ARROW_UTF8:
            return NANOARROW_TYPE_STRING;
        default:
            ps_raise_cstr(PS_VALUEERROR, "arrow: the dtype is not one of INT32, INT64, FLOAT32, FLOAT64, BOOL or UTF8.");
            return NANOARROW_TYPE_INT32; /* unreachable */
    }
}

static ps_arrow_array *ps_arrow_live(void *handle) {
    ps_arrow_array *array = handle;
    if (array == NULL || array->released) {
        ps_raise_cstr(PS_VALUEERROR, "arrow: the array handle was already released.");
    }
    return array;
}

/* An Arrow array view over the handle's C Data pair, validated. The view is
   the read path every accessor shares. */
static void ps_arrow_view(ps_arrow_array *array, struct ArrowArrayView *view) {
    ArrowArrayViewInitFromSchema(view, &array->schema, NULL);
    if (ArrowArrayViewSetArray(view, &array->array, NULL) != NANOARROW_OK) {
        ArrowArrayViewReset(view);
        ps_raise_cstr(PS_RUNTIMEERROR, "arrow: the array could not be read.");
    }
    if (ArrowArrayViewValidate(view, NANOARROW_VALIDATION_LEVEL_DEFAULT, NULL) != NANOARROW_OK) {
        ArrowArrayViewReset(view);
        ps_raise_cstr(PS_RUNTIMEERROR, "arrow: the array failed validation.");
    }
}

/* --- import: build an Arrow array from a subset list -------------------- */

/* The value of one list slot as an int64. The slot holds a double for the
   float dtypes and an int for the integer ones; the dtype word says which,
   so the same 8 bytes are read the way the writer wrote them. */
static int64_t ps_arrow_slot_int(PsSeq *values, int64_t index) {
    int64_t cell = 0;
    ps_seq_get(values, index, &cell);
    return cell;
}

static double ps_arrow_slot_double(PsSeq *values, int64_t index) {
    double cell = 0.0;
    ps_seq_get(values, index, &cell);
    return cell;
}

/* The string payload of one list slot (a utf8 element is a PsBuf prefix). */
static void ps_arrow_slot_buf(PsSeq *values, int64_t index, PsBuf *out) {
    ps_seq_get(values, index, out);
}

void *ps_ffi_arrow_import_array(int64_t dtype, void *values_handle) {
    PsSeq *values = values_handle;
    enum ArrowType type = ps_arrow_type(dtype);
    struct ArrowArray array;
    struct ArrowSchema schema;
    struct ArrowError error;

    ArrowArrayInitFromType(&array, type);
    if (ArrowArrayStartAppending(&array) != NANOARROW_OK) {
        ArrowArrayRelease(&array);
        ps_raise_cstr(PS_RUNTIMEERROR, "arrow: the array could not be started.");
    }
    int64_t count = values != NULL ? ps_seq_len(values) : 0;
    for (int64_t i = 0; i < count; i++) {
        ArrowErrorCode rc = NANOARROW_OK;
        if (dtype == PS_ARROW_UTF8) {
            PsBuf buf;
            ps_arrow_slot_buf(values, i, &buf);
            struct ArrowStringView view;
            view.data = buf.data;
            view.size_bytes = buf.len;
            rc = ArrowArrayAppendString(&array, view);
        } else if (dtype == PS_ARROW_FLOAT32 || dtype == PS_ARROW_FLOAT64) {
            double value = ps_arrow_slot_double(values, i);
            if (dtype == PS_ARROW_FLOAT32) {
                rc = ArrowArrayAppendDouble(&array, (double)(float)value);
            } else {
                rc = ArrowArrayAppendDouble(&array, value);
            }
        } else if (dtype == PS_ARROW_BOOL) {
            int64_t cell = ps_arrow_slot_int(values, i);
            rc = ArrowArrayAppendInt(&array, cell != 0 ? 1 : 0);
        } else {
            int64_t cell = ps_arrow_slot_int(values, i);
            if (dtype == PS_ARROW_INT32 && (cell > INT32_MAX || cell < INT32_MIN)) {
                ArrowArrayRelease(&array);
                ps_raise_cstr(PS_VALUEERROR, "arrow: the value does not fit in int32.");
            }
            rc = ArrowArrayAppendInt(&array, cell);
        }
        if (rc != NANOARROW_OK) {
            ArrowArrayRelease(&array);
            ps_raise_cstr(PS_RUNTIMEERROR, "arrow: the value could not be appended.");
        }
    }
    if (ArrowArrayFinishBuildingDefault(&array, &error) != NANOARROW_OK) {
        ArrowArrayRelease(&array);
        ps_arrow_fail_nanoarrow("the array could not be finished", &error);
    }
    ArrowSchemaInit(&schema);
    if (ArrowSchemaSetType(&schema, type) != NANOARROW_OK) {
        ArrowArrayRelease(&array);
        ArrowSchemaRelease(&schema);
        ps_raise_cstr(PS_RUNTIMEERROR, "arrow: the schema could not be set.");
    }

    ps_arrow_array *handle = ps_alloc((int64_t)sizeof *handle);
    handle->array = array;
    handle->schema = schema;
    handle->dtype = dtype;
    handle->released = 0;
    return handle;
}

/* --- accessors ----------------------------------------------------------- */

int64_t ps_ffi_arrow_array_length(void *handle) {
    ps_arrow_array *array = ps_arrow_live(handle);
    return array->array.length;
}

double ps_ffi_arrow_array_sum(void *handle) {
    ps_arrow_array *array = ps_arrow_live(handle);
    struct ArrowArrayView view;
    ps_arrow_view(array, &view);
    double total = 0.0;
    for (int64_t i = 0; i < view.length; i++) {
        if (ArrowArrayViewIsNull(&view, i)) {
            continue;
        }
        if (array->dtype == PS_ARROW_UTF8) {
            struct ArrowStringView text = ArrowArrayViewGetStringUnsafe(&view, i);
            total += (double)text.size_bytes;
        } else if (array->dtype == PS_ARROW_FLOAT32 || array->dtype == PS_ARROW_FLOAT64) {
            total += ArrowArrayViewGetDoubleUnsafe(&view, i);
        } else {
            total += (double)ArrowArrayViewGetIntUnsafe(&view, i);
        }
    }
    ArrowArrayViewReset(&view);
    return total;
}

double ps_ffi_arrow_array_get(void *handle, int64_t index) {
    ps_arrow_array *array = ps_arrow_live(handle);
    struct ArrowArrayView view;
    ps_arrow_view(array, &view);
    if (index < 0 || index >= view.length) {
        ArrowArrayViewReset(&view);
        ps_raise_cstr(PS_INDEXERROR, "arrow: the index is out of range.");
    }
    double value = 0.0;
    if (array->dtype == PS_ARROW_UTF8) {
        struct ArrowStringView text = ArrowArrayViewGetStringUnsafe(&view, index);
        value = (double)text.size_bytes;
    } else if (array->dtype == PS_ARROW_FLOAT32 || array->dtype == PS_ARROW_FLOAT64) {
        value = ArrowArrayViewGetDoubleUnsafe(&view, index);
    } else {
        value = (double)ArrowArrayViewGetIntUnsafe(&view, index);
    }
    ArrowArrayViewReset(&view);
    return value;
}

int32_t ps_ffi_arrow_array_is_null(void *handle, int64_t index) {
    ps_arrow_array *array = ps_arrow_live(handle);
    struct ArrowArrayView view;
    ps_arrow_view(array, &view);
    if (index < 0 || index >= view.length) {
        ArrowArrayViewReset(&view);
        ps_raise_cstr(PS_INDEXERROR, "arrow: the index is out of range.");
    }
    int8_t is_null = ArrowArrayViewIsNull(&view, index);
    ArrowArrayViewReset(&view);
    return is_null ? 1 : 0;
}

/* --- release ------------------------------------------------------------- */

void ps_ffi_arrow_array_release(void *handle) {
    ps_arrow_array *array = handle;
    if (array == NULL || array->released) {
        return; /* releasing twice is a no-op */
    }
    array->released = 1;
    ArrowArrayRelease(&array->array);
    ArrowSchemaRelease(&array->schema);
}


/* --- the C Data exchange --------------------------------------------------

The manifest's `import_array` builds the struct pair and the accessors read
it. The exchange with a live producer (PyArrow, Polars, nanoarrow under
CPython) happens over the struct pair directly: the producer fills an
ArrowArray and an ArrowSchema, the consumer reads them, and each side calls
the other's release callback. These two entry points are the host shim's
side of that handshake, so the acceptance test can hand a struct pair the
compiled program built to a real Arrow consumer, and read one a real Arrow
producer exported. They are not manifest rows: the C Data structs are plain
memory, not a subset value, so the manifest's `py` vocabulary has no shape
for them (the divergence ledger records the gap). */

/* Fill `out_array` and `out_schema` with a copy of the handle's struct
   pair. The copy owns its own buffers, so the consumer's release callback
   frees the copy and the handle stays valid: the compiled program can keep
   reading the array after the exchange. */
int ps_arrow_export_c_data(void *handle, struct ArrowArray *out_array, struct ArrowSchema *out_schema) {
    ps_arrow_array *array = ps_arrow_live(handle);
    /* The struct pair moves out: the consumer owns it and calls the release
       callback when it is done. The handle is empty afterwards, which the
       accessors report as a released handle (the sqlite pilot's rule: a
       handle is consumed once). */
    ArrowArrayMove(&array->array, out_array);
    ArrowSchemaMove(&array->schema, out_schema);
    array->released = 1;
    return 0;
}

/* Import a struct pair a producer exported: the handle takes ownership and
   calls the producer's release callback when the handle is released. */
int ps_arrow_import_c_data(struct ArrowArray *in_array, struct ArrowSchema *in_schema) {
    if (in_array == NULL || in_array->release == NULL || in_schema == NULL || in_schema->release == NULL) {
        ps_raise_cstr(PS_VALUEERROR, "arrow: the struct pair is empty; the producer released it already.");
    }
    ps_arrow_array *handle = ps_alloc((int64_t)sizeof *handle);
    handle->array = *in_array;
    handle->schema = *in_schema;
    handle->dtype = 0; /* the producer's dtype, discovered by the accessors */
    handle->released = 0;
    /* The producer's struct is moved: the caller's copy is zeroed, so a
       double release is impossible. */
    in_array->release = NULL;
    in_schema->release = NULL;
    return 0;
}
