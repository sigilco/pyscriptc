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
