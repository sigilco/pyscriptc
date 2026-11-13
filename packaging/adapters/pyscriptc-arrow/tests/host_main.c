/* Host harness for the pyscriptc-arrow thunk: runs the sequence the CPython
   differential test also runs and prints the values in one canonical form.
   Linked with the runtime C files (no island), the thunk, the vendored
   nanoarrow and this file.

   The harness is the C-side of the acceptance test: the Python side reads
   the exported bytes with PyArrow and checks the values match. */

#include <stdio.h>
#include <string.h>

#include "nanoarrow/nanoarrow.h"
#include "ps_rt.h"

void *ps_ffi_arrow_import_array(int64_t dtype, void *values);
int64_t ps_ffi_arrow_array_length(void *array);
double ps_ffi_arrow_array_sum(void *array);
double ps_ffi_arrow_array_get(void *array, int64_t index);
int32_t ps_ffi_arrow_array_is_null(void *array, int64_t index);
void ps_ffi_arrow_export_array(void *array, int64_t out_len, const char *out_data);
void ps_ffi_arrow_array_release(void *array);
void *ps_arrow_export_c_data(void *handle, struct ArrowArray *out_array, struct ArrowSchema *out_schema);
void *ps_arrow_import_c_data(struct ArrowArray *in_array, struct ArrowSchema *in_schema);

/* The dtype words the manifest's constants carry. */
#define PS_ARROW_INT32 1
#define PS_ARROW_INT64 2
#define PS_ARROW_FLOAT32 3
#define PS_ARROW_FLOAT64 4
#define PS_ARROW_BOOL 5
#define PS_ARROW_UTF8 6

/* A list of ints, the shape `import_array` takes for the integer dtypes. */
static PsSeq *int_list(const int64_t *values, int64_t count) {
    PsSeq *seq = ps_seq_new((int64_t)sizeof(int64_t), 0);
    for (int64_t i = 0; i < count; i++) {
        int64_t value = values[i];
        ps_seq_push(seq, &value);
    }
    return seq;
}

/* A list of doubles, the shape the float dtypes take. */
static PsSeq *float_list(const double *values, int64_t count) {
    PsSeq *seq = ps_seq_new((int64_t)sizeof(double), 0);
    for (int64_t i = 0; i < count; i++) {
        double value = values[i];
        ps_seq_push(seq, &value);
    }
    return seq;
}

/* A list of strings, the shape the utf8 dtype takes: each slot is a PsBuf
   prefix (16 bytes), which is the runtime's str element layout. */
static PsSeq *str_list(const char *const *values, int64_t count) {
    PsSeq *seq = ps_seq_new((int64_t)sizeof(PsBuf), 0);
    for (int64_t i = 0; i < count; i++) {
        PsBuf buf;
        ps_buf_copy(&buf, values[i], (int64_t)strlen(values[i]));
        ps_seq_push(seq, &buf);
    }
    return seq;
}


int main(void) {
    /* int32 */
    {
        const int64_t values[] = {1, 2, 3, 40};
        void *array = ps_ffi_arrow_import_array(PS_ARROW_INT32, int_list(values, 4));
        printf("int32_length=%lld\n", (long long)ps_ffi_arrow_array_length(array));
        printf("int32_sum=%g\n", ps_ffi_arrow_array_sum(array));
        printf("int32_get0=%g\n", ps_ffi_arrow_array_get(array, 0));
        printf("int32_get3=%g\n", ps_ffi_arrow_array_get(array, 3));
        printf("int32_null0=%d\n", ps_ffi_arrow_array_is_null(array, 0));
        ps_ffi_arrow_array_release(array);
    }
    /* int64 */
    {
        const int64_t values[] = {-5, 0, 1000000007};
        void *array = ps_ffi_arrow_import_array(PS_ARROW_INT64, int_list(values, 3));
        printf("int64_length=%lld\n", (long long)ps_ffi_arrow_array_length(array));
        printf("int64_sum=%g\n", ps_ffi_arrow_array_sum(array));
        printf("int64_get0=%g\n", ps_ffi_arrow_array_get(array, 0));
        ps_ffi_arrow_array_release(array);
    }
    /* float64 */
    {
        const double values[] = {0.5, -1.25, 3.0};
        void *array = ps_ffi_arrow_import_array(PS_ARROW_FLOAT64, float_list(values, 3));
        printf("float64_length=%lld\n", (long long)ps_ffi_arrow_array_length(array));
        printf("float64_sum=%g\n", ps_ffi_arrow_array_sum(array));
        printf("float64_get1=%g\n", ps_ffi_arrow_array_get(array, 1));
        ps_ffi_arrow_array_release(array);
    }
    /* float32 rounds the value to the narrower dtype. */
    {
        const double values[] = {0.1, 0.2};
        void *array = ps_ffi_arrow_import_array(PS_ARROW_FLOAT32, float_list(values, 2));
        printf("float32_get0=%g\n", ps_ffi_arrow_array_get(array, 0));
        ps_ffi_arrow_array_release(array);
    }
    /* bool */
    {
        const int64_t values[] = {1, 0, 1};
        void *array = ps_ffi_arrow_import_array(PS_ARROW_BOOL, int_list(values, 3));
        printf("bool_get0=%g\n", ps_ffi_arrow_array_get(array, 0));
        printf("bool_get1=%g\n", ps_ffi_arrow_array_get(array, 1));
        ps_ffi_arrow_array_release(array);
    }
    /* utf8: the accessor reports the byte length, the stream reports the
       text. */
    {
        const char *const values[] = {"ada", "bo"};
        void *array = ps_ffi_arrow_import_array(PS_ARROW_UTF8, str_list(values, 2));
        printf("utf8_length=%lld\n", (long long)ps_ffi_arrow_array_length(array));
        printf("utf8_get0=%g\n", ps_ffi_arrow_array_get(array, 0));
        ps_ffi_arrow_array_release(array);
    }
    /* The exchange: export the struct pair, import it back. */
    {
        const int64_t values[] = {7, 8, 9};
        void *array = ps_ffi_arrow_import_array(PS_ARROW_INT64, int_list(values, 3));
        struct ArrowArray out_array;
        struct ArrowSchema out_schema;
        memset(&out_array, 0, sizeof out_array);
        memset(&out_schema, 0, sizeof out_schema);
        ps_arrow_export_c_data(array, &out_array, &out_schema);
        printf("exported_length=%lld\n", (long long)out_array.length);
        printf("exported_format=%s\n", out_schema.format);
        struct ArrowArray in_array;
        struct ArrowSchema in_schema;
        in_array = out_array;
        in_schema = out_schema;
        void *back = ps_arrow_import_c_data(&in_array, &in_schema);
        printf("imported_length=%lld\n", (long long)ps_ffi_arrow_array_length(back));
        printf("imported_sum=%g\n", ps_ffi_arrow_array_sum(back));
        ps_ffi_arrow_array_release(back);
    }
    return 0;
}
