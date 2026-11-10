/* csv: eager excel-dialect reader plus an excel-dialect writer.
   reader returns list[list[str]] from a text file or a list[str] of
   physical lines: quoted fields keep embedded newlines, "" inside quotes
   is a literal quote, and a quote only opens a field when it is the
   field's first character. A blank record parses to a zero-field row.
   writer emits \r\n-terminated rows with minimal quoting. */
#include "ps_rt.h"

#include <stdlib.h>
#include <string.h>

typedef struct {
    PsSeq *rows; /* elem = PsSeq* */
    PsSeq *row;  /* elem = PsBuf */
    PsGrow field;
    int in_quotes;
    int started; /* the current field has begun (chars or a quote seen) */
    char delim;
} PsCsv;

static void csv_init(PsCsv *st, char delim) {
    st->rows = ps_seq_new((int64_t)sizeof(PsSeq *), 0);
    st->row = ps_seq_new((int64_t)sizeof(PsBuf), 0);
    ps_grow_init(&st->field);
    st->in_quotes = 0;
    st->started = 0;
    st->delim = delim;
}

static void csv_end_field(PsCsv *st) {
    PsBuf b;
    ps_grow_finish(&st->field, &b);
    ps_grow_init(&st->field);
    ps_seq_push(st->row, &b);
    st->started = 0;
}

static void csv_end_record(PsCsv *st) {
    if (st->row->len > 0 || st->field.len > 0 || st->started) {
        csv_end_field(st);
    }
    ps_seq_push(st->rows, &st->row);
    st->row = ps_seq_new((int64_t)sizeof(PsBuf), 0);
}

static void csv_feed(PsCsv *st, const char *p, int64_t n) {
    int64_t i;
    for (i = 0; i < n; i++) {
        char c = p[i];
        if (st->in_quotes) {
            if (c == '"') {
                if (i + 1 < n && p[i + 1] == '"') {
                    ps_grow_byte(&st->field, '"');
                    i++;
                } else {
                    st->in_quotes = 0;
                }
            } else {
                ps_grow_byte(&st->field, (unsigned char)c);
            }
            continue;
        }
        if (c == st->delim) {
            csv_end_field(st);
            continue;
        }
        if (c == '\r' || c == '\n') {
            if (c == '\r' && i + 1 < n && p[i + 1] == '\n') {
                i++;
            }
            csv_end_record(st);
            continue;
        }
        if (c == '"' && st->field.len == 0 && !st->started) {
            st->in_quotes = 1;
            st->started = 1;
            continue;
        }
        st->started = 1;
        ps_grow_byte(&st->field, (unsigned char)c);
    }
}

static void csv_flush(PsCsv *st) {
    if (st->row->len > 0 || st->field.len > 0 || st->started) {
        csv_end_record(st);
    }
}

PsSeq *ps_csv_read_io(PsIO *io, int64_t delim_len, const char *delim_data) {
    PsBuf text;
    PsCsv st;
    char delim = ',';
    if (delim_len == 1 && delim_data != NULL) {
        delim = delim_data[0];
    } else if (delim_len != 0) {
        ps_raise_cstr(PS_VALUE, "\"delimiter\" must be a 1-character string");
    }
    ps_io_read(io, &text);
    csv_init(&st, delim);
    csv_feed(&st, text.data == NULL ? "" : text.data, text.len);
    csv_flush(&st);
    return st.rows;
}

PsSeq *ps_csv_read_lines(PsSeq *lines, int64_t delim_len, const char *delim_data) {
    PsCsv st;
    int64_t i;
    char delim = ',';
    if (delim_len == 1 && delim_data != NULL) {
        delim = delim_data[0];
    } else if (delim_len != 0) {
        ps_raise_cstr(PS_VALUE, "\"delimiter\" must be a 1-character string");
    }
    csv_init(&st, delim);
    for (i = 0; i < lines->len; i++) {
        PsBuf line;
        ps_seq_get(lines, i, &line);
        csv_feed(&st, line.data, line.len);
        if (!st.in_quotes) {
            csv_end_record(&st);
        }
    }
    csv_flush(&st);
    return st.rows;
}

PsIO *ps_csv_writer(PsIO *io) { return io; }

static int csv_needs_quote(const char *p, int64_t n) {
    int64_t j;
    for (j = 0; j < n; j++) {
        char c = p[j];
        if (c == ',' || c == '"' || c == '\r' || c == '\n') {
            return 1;
        }
    }
    return 0;
}

void ps_csv_writerow(PsIO *io, PsSeq *row) {
    int64_t i;
    for (i = 0; i < row->len; i++) {
        PsBuf f;
        ps_seq_get(row, i, &f);
        if (i > 0) {
            ps_io_write(io, 1, ",");
        }
        if (f.len > 0 && csv_needs_quote(f.data, f.len)) {
            int64_t j;
            ps_io_write(io, 1, "\"");
            for (j = 0; j < f.len; j++) {
                if (f.data[j] == '"') {
                    ps_io_write(io, 2, "\"\"");
                } else {
                    ps_io_write(io, 1, f.data + j);
                }
            }
            ps_io_write(io, 1, "\"");
        } else {
            ps_io_write(io, f.len, f.data == NULL ? "" : f.data);
        }
    }
    ps_io_write(io, 2, "\r\n");
}

void ps_csv_writerows(PsIO *io, PsSeq *rows) {
    int64_t i;
    for (i = 0; i < rows->len; i++) {
        PsSeq *row;
        ps_seq_get(rows, i, &row);
        ps_csv_writerow(io, row);
    }
}

/* --- DictReader and DictWriter -----------------------------------------
   DictReader maps the header row onto every record: the row is a dict from
   a fieldname to the field text, a short record fills the missing fields
   with the restval (None by default) and a long record raises the
   ValueError CPython raises. DictWriter takes the fieldnames once and
   writes the header row and the rows of a dict.

   The row shape is dict[str, str | None]: the values ride the tagged
   opt-str cell the compiler spells for `str | None` ({ i1 tag, %ps.buf
   val }, the cell pointer is the map slot), so a short record stores the
   None cell where CPython stores None. DictWriter's row dict is plain
   dict[str, str] (its restval default is the empty text). */

typedef struct {
    PsIO *io;
    PsSeq *fields; /* elem = PsBuf: the fieldnames, in order */
    PsSeq *rows;   /* elem = PsMap*: the parsed rows */
    int64_t index; /* the next row the cursor reads */
} PsCsvDict;

/* The layout of the opt-str cell the compiler spells for `str | None`:
   { i1 tag, %ps.buf val }. */
enum { CSV_OPT_SIZE = 24, CSV_OPT_VAL = 8 };

/* The None cell: the tag stays clear. */
static void *csv_opt_none(void) {
    void *cell = ps_alloc(CSV_OPT_SIZE);
    memset(cell, 0, CSV_OPT_SIZE);
    return cell;
}

/* The str cell: the tag set, the text in the val buffer. */
static void *csv_opt_str(PsBuf val) {
    void *cell = ps_alloc(CSV_OPT_SIZE);
    memset(cell, 0, CSV_OPT_SIZE);
    *(unsigned char *)cell = 1;
    PsBuf *slot = (PsBuf *)((char *)cell + CSV_OPT_VAL);
    *slot = val;
    return cell;
}

/* The map a row of `names` and `values` holds: the keys are the field
   names, the values the tagged cells (one pointer per slot). */
static PsMap *csv_dict_new(PsSeq *names, PsSeq *cells, int64_t count) {
    PsMap *m = ps_map_new(2, (int64_t)sizeof(PsBuf), (int64_t)sizeof(void *), count);
    for (int64_t i = 0; i < count; i++) {
        PsBuf key;
        void *cell;
        ps_seq_get(names, i, &key);
        ps_seq_get(cells, i, &cell);
        ps_map_put(m, &key, &cell);
    }
    return m;
}

/* The fieldnames of a DictReader: the first non-blank record of the
   source (CPython skips the blank records the underlying reader yields
   when it picks the header too). */
static int64_t csv_dict_header(PsSeq *rows) {
    for (int64_t i = 0; i < rows->len; i++) {
        PsSeq *record;
        ps_seq_get(rows, i, &record);
        if (record->len > 0) {
            return i;
        }
    }
    return -1;
}

static PsSeq *csv_dict_fieldnames(PsSeq *rows) {
    PsSeq *names = ps_seq_new((int64_t)sizeof(PsBuf), 0);
    int64_t head = csv_dict_header(rows);
    if (head < 0) {
        return names;
    }
    PsSeq *first;
    ps_seq_get(rows, head, &first);
    for (int64_t i = 0; i < first->len; i++) {
        PsBuf field;
        ps_seq_get(first, i, &field);
        ps_seq_push(names, &field);
    }
    return names;
}

/* The row maps: the first non-blank record names the fields, the rest
   carry the values. A record with more fields than the header raises the
   ValueError the seam documents (CPython stores the extras under the None
   restkey, which is outside the row dict's type); a record with fewer
   fills the missing fields with the None cell (the restval default), and
   a blank record is skipped, as CPython's DictReader skips it. */
static PsSeq *csv_dict_rows(PsSeq *rows, PsSeq *names) {
    PsSeq *out = ps_seq_new((int64_t)sizeof(PsMap *), 0);
    int64_t width = names->len;
    void *none = csv_opt_none();
    int64_t head = csv_dict_header(rows);
    for (int64_t i = head + 1; i < rows->len; i++) {
        PsSeq *record;
        ps_seq_get(rows, i, &record);
        if (record->len == 0) {
            /* CPython's DictReader skips the blank records the underlying
               reader yields: a dict full of None values is not useful. */
            continue;
        }
        if (record->len > width) {
            ps_raise_cstr(PS_VALUE, "dict reader has more fields than fieldnames");
        }
        PsSeq *cells = ps_seq_new((int64_t)sizeof(void *), 0);
        for (int64_t j = 0; j < width; j++) {
            void *cell = none;
            if (j < record->len) {
                PsBuf field;
                ps_seq_get(record, j, &field);
                cell = csv_opt_str(field);
            }
            ps_seq_push(cells, &cell);
        }
        PsMap *row = csv_dict_new(names, cells, width);
        ps_seq_push(out, &row);
    }
    return out;
}

PsSeq *ps_csv_dict_reader(PsIO *io, int64_t delim_len, const char *delim_data) {
    PsSeq *records = ps_csv_read_io(io, delim_len, delim_data);
    return csv_dict_rows(records, csv_dict_fieldnames(records));
}

PsSeq *ps_csv_dict_reader_lines(PsSeq *lines, int64_t delim_len, const char *delim_data) {
    PsSeq *records = ps_csv_read_lines(lines, delim_len, delim_data);
    return csv_dict_rows(records, csv_dict_fieldnames(records));
}

/* The DictWriter handle: the file handle and the fieldnames. */
void *ps_csv_dict_writer(PsIO *io, PsSeq *fields) {
    PsCsvDict *d = ps_alloc((int64_t)sizeof(PsCsvDict));
    d->io = io;
    d->fields = fields;
    d->rows = NULL;
    d->index = 0;
    return d;
}

/* The header row: the fieldnames, in order. */
void ps_csv_dict_writeheader(void *handle) {
    PsCsvDict *d = handle;
    ps_csv_writerow(d->io, d->fields);
}

/* One row: the field texts, in fieldname order. A field the row does not
   carry takes the restval, the empty text CPython's default restval is.
   The restval keyword is outside the subset and fenced by name. */
void ps_csv_dict_writerow(void *handle, PsMap *row) {
    PsCsvDict *d = handle;
    PsSeq *values = ps_seq_new((int64_t)sizeof(PsBuf), 0);
    for (int64_t i = 0; i < d->fields->len; i++) {
        PsBuf key;
        PsBuf val;
        ps_seq_get(d->fields, i, &key);
        if (!ps_map_has(row, &key)) {
            memset(&val, 0, sizeof val);
            val.len = 0;
            val.data = ps_alloc(1);
            val.data[0] = '\0';
        } else {
            ps_map_get(row, &key, &val);
        }
        ps_seq_push(values, &val);
    }
    ps_csv_writerow(d->io, values);
}

/* The rows of the writer: the field count, so a row the caller builds
   with the wrong width raises the ValueError CPython raises. */
int64_t ps_csv_dict_field_count(void *handle) {
    PsCsvDict *d = handle;
    return d->fields->len;
}
