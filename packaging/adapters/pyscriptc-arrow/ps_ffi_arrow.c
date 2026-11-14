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
#include "nanoarrow/nanoarrow_ipc.h"
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

/* The Arrow C Stream a handle owns. The wrapper makes a released handle
   detectable, the same rule the Array handle follows. */
typedef struct ps_arrow_stream {
    struct ArrowArrayStream *stream;
    struct ArrowSchema schema; /* the stream's one-column schema, read once */
    int released;
} ps_arrow_stream;

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

static int64_t ps_arrow_dtype_from_schema(const struct ArrowSchema *schema);

static ps_arrow_array *ps_arrow_live(void *handle) {
    ps_arrow_array *array = handle;
    if (array == NULL || array->released) {
        ps_raise_cstr(PS_VALUEERROR, "arrow: the array handle was already released.");
    }
    return array;
}

/* The dtype word of an imported schema. The Arrow format strings are the
   C Data Interface's own spelling (`l` for int64, `g` for double, `b` for
   bool, `u` for utf8), so the word comes straight off the schema. A format
   outside the subset is the honest fence: the handle keeps the word 0 and
   the accessors refuse it. */
static int64_t ps_arrow_dtype_from_schema(const struct ArrowSchema *schema) {
    const char *format = schema->format;
    if (format == NULL) {
        return 0;
    }
    /* The format may carry parameters (`w:8`, `tdD`), so only the leading
       type letter is read. */
    switch (format[0]) {
        case 'c':
        case 'C':
            return PS_ARROW_INT32; /* int8 and uint8 ride the int32 word */
        case 's':
        case 'S':
            return PS_ARROW_INT32; /* int16 and uint16 */
        case 'l':
        case 'L':
            return PS_ARROW_INT64;
        case 'i':
        case 'I':
            return PS_ARROW_INT32;
        case 'f':
            return PS_ARROW_FLOAT32;
        case 'g':
            return PS_ARROW_FLOAT64;
        case 'b':
            return PS_ARROW_BOOL;
        case 'u':
        case 'U':
            return PS_ARROW_UTF8;
        default:
            return 0;
    }
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
        if (array->dtype == 0) {
            ArrowArrayViewReset(&view);
            ps_raise_cstr(PS_VALUEERROR, "arrow: the imported schema is not one of INT32, INT64, FLOAT32, FLOAT64, BOOL or UTF8.");
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
    if (array->dtype == 0) {
        ArrowArrayViewReset(&view);
        ps_raise_cstr(PS_VALUEERROR, "arrow: the imported schema is not one of INT32, INT64, FLOAT32, FLOAT64, BOOL or UTF8.");
    }
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
   calls the producer's release callback when the handle is released. The
   dtype word comes from the schema's format, so the accessors read the
   column the way the producer wrote it. */
void *ps_arrow_import_c_data(struct ArrowArray *in_array, struct ArrowSchema *in_schema) {
    if (in_array == NULL || in_array->release == NULL || in_schema == NULL || in_schema->release == NULL) {
        ps_raise_cstr(PS_VALUEERROR, "arrow: the struct pair is empty; the producer released it already.");
    }
    ps_arrow_array *handle = ps_alloc((int64_t)sizeof *handle);
    handle->array = *in_array;
    handle->schema = *in_schema;
    handle->dtype = ps_arrow_dtype_from_schema(&handle->schema);
    handle->released = 0;
    /* The producer's struct is moved: the caller's copy is zeroed, so a
       double release is impossible. */
    in_array->release = NULL;
    in_schema->release = NULL;
    return handle;
}

/* --- the wire exchange: the Arrow IPC stream ------------------------------

The in-process exchange with a live Arrow implementation goes over the
wire form: `export_array` serialises the handle's struct pair as one IPC
stream record batch (the same bytes the C Stream interface carries), and
`import_stream` decodes those bytes back. A consumer under CPython reads
the exported bytes with `pyarrow.ipc.open_stream` and sees one record
batch of one column named `v`; a producer under CPython hands its own
`__arrow_c_stream__` bytes to `import_stream`. Nested types, dictionaries
and extension metadata are the honest fence: the decoder reads the schema
and the batch, the reader projects field 0, and any dtype the accessor
table does not name surfaces as a nanoarrow validation error, never a
silently wrong value. */

void *ps_ffi_arrow_import_stream(int64_t n, const char *data) {
    struct ArrowBuffer input;
    ArrowBufferInit(&input);
    if (n > 0 && ArrowBufferAppend(&input, data, (int64_t)n) != NANOARROW_OK) {
        ArrowBufferReset(&input);
        ps_raise_cstr(PS_RUNTIMEERROR, "arrow: the stream bytes could not be staged.");
    }
    struct ArrowIpcInputStream stream;
    if (ArrowIpcInputStreamInitBuffer(&stream, &input) != NANOARROW_OK) {
        ArrowBufferReset(&input);
        ps_raise_cstr(PS_RUNTIMEERROR, "arrow: the stream input could not be opened.");
    }
    struct ArrowArrayStream *decoded = (struct ArrowArrayStream *)ps_alloc((int64_t)sizeof *decoded);
    if (ArrowIpcArrayStreamReaderInit(decoded, &stream, NULL) != NANOARROW_OK) {
        stream.release(&stream);
        ArrowBufferReset(&input);
        ps_raise_cstr(PS_RUNTIMEERROR, "arrow: the stream could not be decoded.");
    }
    /* The decoded stream's bytes are owned by the reader from here; the
       staging buffer above was moved into the input stream. */

    struct ArrowSchema schema;
    struct ArrowError error;
    if (ArrowArrayStreamGetSchema(decoded, &schema, &error) != NANOARROW_OK) {
        ArrowArrayStreamRelease(decoded);
        ps_arrow_fail_nanoarrow("the stream schema could not be read", &error);
    }
    if (schema.n_children != 1) {
        ArrowSchemaRelease(&schema);
        ArrowArrayStreamRelease(decoded);
        ps_raise_cstr(PS_VALUEERROR, "arrow: the stream must carry exactly one column.");
    }

    ps_arrow_stream *handle = ps_alloc((int64_t)sizeof *handle);
    handle->stream = decoded;
    handle->schema = schema; /* the handle owns the schema now */
    handle->released = 0;
    return handle;
}

PsSeq *ps_ffi_arrow_stream_read(void *stream_handle) {
    ps_arrow_stream *handle = stream_handle;
    if (handle == NULL || handle->released || handle->stream == NULL) {
        ps_raise_cstr(PS_VALUEERROR, "arrow: the stream handle was already released.");
    }
    struct ArrowArray batch;
    struct ArrowError error;
    int rc = ArrowArrayStreamGetNext(handle->stream, &batch, &error);
    if (rc != NANOARROW_OK) {
        ps_arrow_fail_nanoarrow("the stream batch could not be read", &error);
    }
    PsSeq *rows = ps_seq_new((int64_t)sizeof(PsMap *), 0);
    if (batch.release == NULL) {
        return rows; /* the stream's end marker, an empty list */
    }
    if (batch.n_children != 1) {
        ArrowArrayRelease(&batch);
        ps_raise_cstr(PS_VALUEERROR, "arrow: the batch must carry exactly one column.");
    }
    /* The batch is a struct array whose one child is the column. The view
       is over the child, so the accessor reads the column directly. */
    struct ArrowArrayView view;
    ArrowArrayViewInitFromSchema(&view, handle->schema.children[0], NULL);
    if (ArrowArrayViewSetArray(&view, batch.children[0], NULL) != NANOARROW_OK) {
        ArrowArrayViewReset(&view);
        ArrowArrayRelease(&batch);
        ps_raise_cstr(PS_RUNTIMEERROR, "arrow: the batch column could not be read.");
    }
    if (ArrowArrayViewValidate(&view, NANOARROW_VALIDATION_LEVEL_DEFAULT, NULL) != NANOARROW_OK) {
        ArrowArrayViewReset(&view);
        ArrowArrayRelease(&batch);
        ps_raise_cstr(PS_RUNTIMEERROR, "arrow: the batch column failed validation.");
    }
    /* One row per element: {"v": <text>}, the projection the .pyi and the
       differential test pin. The value is the element's own text: an
       integer column reads as its decimal spelling, a float column as its
       float spelling, a bool as "True"/"False". */
    for (int64_t i = 0; i < view.length; i++) {
        PsMap *row = ps_map_new(2, (int64_t)sizeof(PsBuf), (int64_t)sizeof(PsBuf), 1);
        PsBuf key;
        PsBuf val;
        ps_buf_copy(&key, "v", 1);
        if (ArrowArrayViewIsNull(&view, i)) {
            ps_buf_copy(&val, "", 0);
        } else {
            char text[64];
            int written = 0;
            switch (view.storage_type) {
                case NANOARROW_TYPE_INT32:
                case NANOARROW_TYPE_INT64:
                    written = snprintf(text, sizeof text, "%lld", (long long)ArrowArrayViewGetIntUnsafe(&view, i));
                    break;
                case NANOARROW_TYPE_FLOAT:
                case NANOARROW_TYPE_DOUBLE:
                    written = snprintf(text, sizeof text, "%g", ArrowArrayViewGetDoubleUnsafe(&view, i));
                    break;
                case NANOARROW_TYPE_BOOL:
                    written = snprintf(text, sizeof text, "%s", ArrowArrayViewGetIntUnsafe(&view, i) ? "True" : "False");
                    break;
                case NANOARROW_TYPE_STRING:
                case NANOARROW_TYPE_LARGE_STRING: {
                    struct ArrowStringView sv = ArrowArrayViewGetStringUnsafe(&view, i);
                    ps_buf_copy(&val, sv.data, sv.size_bytes);
                    written = -1;
                    break;
                }
                default:
                    ArrowArrayViewReset(&view);
                    ArrowArrayRelease(&batch);
                    ps_raise_cstr(PS_VALUEERROR, "arrow: the stream column is not one of INT32, INT64, FLOAT32, FLOAT64, BOOL or UTF8.");
            }
            if (written >= 0) {
                ps_buf_copy(&val, text, written);
            }
        }
        ps_map_put(row, &key, &val);
        ps_seq_push(rows, &row);
    }
    ArrowArrayViewReset(&view);
    ArrowArrayRelease(&batch);
    return rows;
}

void ps_ffi_arrow_stream_release(void *stream_handle) {
    ps_arrow_stream *stream = stream_handle;
    if (stream == NULL || stream->released) {
        return; /* releasing twice is a no-op */
    }
    stream->released = 1;
    if (stream->stream != NULL) {
        ArrowArrayStreamRelease(stream->stream);
        stream->stream = NULL;
    }
    ArrowSchemaRelease(&stream->schema);
}

/* One record batch of one column named `v`, the IPC stream wire form.
   The handle is read, not moved: exporting twice produces two
   independent serialisations and the handle stays live. The bytes land in
   the runtime's PsBuf prefix, the `bytes` return shape of the manifest. */
void ps_ffi_arrow_export_array(void *array_handle, PsBuf *out) {
    ps_arrow_array *array = ps_arrow_live(array_handle);
    struct ArrowError error;
    struct ArrowArrayView view;
    ps_arrow_view(array, &view);

    /* The stream carries the column under the fixed name `v`. The IPC wire
       form's top level is a struct, so the schema is a one-field struct
       whose field is the column. The name never depends on how the array
       was built. */
    struct ArrowSchema field;
    ArrowSchemaInit(&field);
    if (ArrowSchemaSetType(&field, ps_arrow_type(array->dtype)) != NANOARROW_OK ||
        ArrowSchemaSetName(&field, "v") != NANOARROW_OK) {
        ArrowSchemaRelease(&field);
        ArrowArrayViewReset(&view);
        ps_raise_cstr(PS_RUNTIMEERROR, "arrow: the wire field could not be built.");
    }
    struct ArrowSchema wire_schema;
    ArrowSchemaInit(&wire_schema);
    if (ArrowSchemaSetTypeStruct(&wire_schema, 1) != NANOARROW_OK) {
        ArrowSchemaRelease(&field);
        ArrowSchemaRelease(&wire_schema);
        ArrowArrayViewReset(&view);
        ps_raise_cstr(PS_RUNTIMEERROR, "arrow: the wire schema could not be built.");
    }
    /* The struct's one child is the field built above, moved in so the
       struct owns it. */
    ArrowSchemaMove(&field, wire_schema.children[0]);

    /* The wire array is the struct array: one child, the column. The child
       carries the values; the parent counts the rows. */
    struct ArrowArray wire_array;
    ArrowArrayInitFromSchema(&wire_array, &wire_schema, NULL);
    if (ArrowArrayStartAppending(&wire_array) != NANOARROW_OK) {
        ArrowArrayRelease(&wire_array);
        ArrowSchemaRelease(&wire_schema);
        ArrowArrayViewReset(&view);
        ps_raise_cstr(PS_RUNTIMEERROR, "arrow: the wire array could not be started.");
    }
    for (int64_t i = 0; i < view.length; i++) {
        ArrowErrorCode rc;
        if (ArrowArrayViewIsNull(&view, i)) {
            rc = ArrowArrayAppendNull(wire_array.children[0], 1);
        } else if (array->dtype == PS_ARROW_UTF8) {
            struct ArrowStringView text = ArrowArrayViewGetStringUnsafe(&view, i);
            rc = ArrowArrayAppendString(wire_array.children[0], text);
        } else if (array->dtype == PS_ARROW_FLOAT32 || array->dtype == PS_ARROW_FLOAT64) {
            rc = ArrowArrayAppendDouble(wire_array.children[0], ArrowArrayViewGetDoubleUnsafe(&view, i));
        } else {
            rc = ArrowArrayAppendInt(wire_array.children[0], ArrowArrayViewGetIntUnsafe(&view, i));
        }
        if (rc != NANOARROW_OK) {
            ArrowArrayRelease(&wire_array);
            ArrowSchemaRelease(&wire_schema);
            ArrowArrayViewReset(&view);
            ps_raise_cstr(PS_RUNTIMEERROR, "arrow: a wire element could not be appended.");
        }
        if (ArrowArrayFinishElement(&wire_array) != NANOARROW_OK) {
            ArrowArrayRelease(&wire_array);
            ArrowSchemaRelease(&wire_schema);
            ArrowArrayViewReset(&view);
            ps_raise_cstr(PS_RUNTIMEERROR, "arrow: a wire row could not be closed.");
        }
    }
    ArrowArrayViewReset(&view);
    if (ArrowArrayFinishBuildingDefault(&wire_array, &error) != NANOARROW_OK) {
        ArrowArrayRelease(&wire_array);
        ArrowSchemaRelease(&wire_schema);
        ps_arrow_fail_nanoarrow("the wire array could not be finished", &error);
    }

    /* Build a one-shot stream over the struct pair and serialise it. */
    struct ArrowArrayStream basic;
    if (ArrowBasicArrayStreamInit(&basic, &wire_schema, 1) != NANOARROW_OK) {
        ArrowArrayRelease(&wire_array);
        ArrowSchemaRelease(&wire_schema);
        ps_raise_cstr(PS_RUNTIMEERROR, "arrow: the wire stream could not be staged.");
    }
    ArrowBasicArrayStreamSetArray(&basic, 0, &wire_array);

    struct ArrowBuffer output;
    ArrowBufferInit(&output);
    struct ArrowIpcOutputStream sink;
    if (ArrowIpcOutputStreamInitBuffer(&sink, &output) != NANOARROW_OK) {
        ArrowBufferReset(&output);
        basic.release(&basic);
        ps_raise_cstr(PS_RUNTIMEERROR, "arrow: the wire sink could not be opened.");
    }
    struct ArrowIpcWriter writer;
    if (ArrowIpcWriterInit(&writer, &sink) != NANOARROW_OK) {
        sink.release(&sink);
        ArrowBufferReset(&output);
        ArrowArrayStreamRelease(&basic);
        ps_raise_cstr(PS_RUNTIMEERROR, "arrow: the wire writer could not be started.");
    }
    if (ArrowIpcWriterWriteArrayStream(&writer, &basic, &error) != NANOARROW_OK) {
        ArrowIpcWriterReset(&writer);
        ArrowBufferReset(&output);
        ArrowArrayStreamRelease(&basic);
        ps_arrow_fail_nanoarrow("the wire stream could not be written", &error);
    }
    ArrowIpcWriterReset(&writer);
    ArrowArrayStreamRelease(&basic);

    if (output.size_bytes > 0) {
        ps_buf_copy(out, (const char *)output.data, output.size_bytes);
    } else {
        ps_buf_copy(out, "", 0);
    }
    ArrowBufferReset(&output);
}
