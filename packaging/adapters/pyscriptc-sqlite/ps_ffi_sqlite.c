/* pyscriptc-sqlite thunk (.agents/docs/ffi/ffi-manifest.md section 5).

The manifest binds `abi = ps_ffi_sqlite_*` here, never the SQLite symbols. This
file owns everything the ABI cannot express: the variadic-free open flags,
out-params, NUL termination, error mapping and the construction of result rows
behind the ps_rt.h layout (PsSeq/PsMap, ps_alloc). Callers see a Connection as
an opaque pointer; no field crosses the boundary.

Strings arrive as (i64 len, ptr) pairs and are not NUL terminated.

The v2 surface (issue #1343) adds parameter binding and typed rows:

  - `execute` and `query` take a `params` list whose elements are tagged
    dynamic values (the PsDyn cell the Any floor already uses). A cell's tag
    names its arm: int, float, str or none. The tag is bound to the `?`
    placeholder in the order the parameters appear, so a NULL parameter binds
    a SQL NULL and a float binds a REAL.
  - `query` returns rows as `dict[str, object]`: the column name is the key
    and the column value is a tagged cell, so an INTEGER column reads back as
    an int, a REAL as a float, TEXT as a str and a SQL NULL as the none arm.
    The dict preserves the column order, so `row.keys()` is the statement's
    column list.
  - `execute` returns the row count of the statement (SQLite's changes() after
    the last step), which is what sqlite3's cursor.rowcount reports for a
    write.
  - `lastrowid` returns the rowid of the last insert on the connection.

Exception surface (mirrored in pyscriptc_sqlite.pyi):
  PS_RUNTIMEERROR  SQLite error or more than one statement; "sqlite3: <text>"
  PS_VALUEERROR    the connection is closed
Rows are list[dict[str, object]]: every column is bound by its name and
carries the value's own type. */

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "ps_rt.h"
#include "vendor/sqlite3.h"

typedef struct ps_sqlite_conn {
    sqlite3 *db; /* NULL once closed */
} ps_sqlite_conn;

static sqlite3 *ps_sqlite_live(ps_sqlite_conn *conn) {
    if (conn == NULL || conn->db == NULL) {
        ps_raise_cstr(PS_VALUEERROR, "Cannot operate on a closed database.");
    }
    return conn->db;
}

/* A NUL terminated copy of a (len, ptr) string; sqlite wants C strings for open. */
static char *ps_sqlite_cstr(int64_t n, const char *p) {
    char *out = malloc((size_t)n + 1);
    if (out == NULL) {
        ps_raise_cstr(PS_MEMORYERROR, "out of memory");
    }
    if (n > 0) {
        memcpy(out, p, (size_t)n);
    }
    out[n] = '\0';
    return out;
}

void *ps_ffi_sqlite_connect(int64_t n, const char *path) {
    char *cpath = ps_sqlite_cstr(n, path);
    sqlite3 *db = NULL;
    int rc = sqlite3_open_v2(cpath, &db, SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE, NULL);
    free(cpath);
    if (rc != SQLITE_OK) {
        char msg[256];
        snprintf(msg, sizeof msg, "sqlite3: %s", db != NULL ? sqlite3_errmsg(db) : sqlite3_errstr(rc));
        sqlite3_close_v2(db);
        ps_raise_cstr(PS_RUNTIMEERROR, msg);
    }
    ps_sqlite_conn *conn = ps_alloc((int64_t)sizeof *conn);
    conn->db = db;
    return conn;
}

int64_t ps_ffi_sqlite_execute(void *handle, int64_t n, const char *sql) {
    sqlite3 *db = ps_sqlite_live(handle);
    char *csql = ps_sqlite_cstr(n, sql);
    char *err = NULL;
    int rc = sqlite3_exec(db, csql, NULL, NULL, &err);
    free(csql);
    if (rc != SQLITE_OK) {
        char msg[256];
        snprintf(msg, sizeof msg, "sqlite3: %s", err != NULL ? err : sqlite3_errstr(rc));
        sqlite3_free(err);
        ps_raise_cstr(PS_RUNTIMEERROR, msg);
    }
    return SQLITE_OK;
}

PsSeq *ps_ffi_sqlite_query(void *handle, int64_t n, const char *sql) {
    sqlite3 *db = ps_sqlite_live(handle);
    sqlite3_stmt *stmt = NULL;
    const char *tail = NULL;
    int rc = sqlite3_prepare_v2(db, sql, (int)n, &stmt, &tail);
    if (rc != SQLITE_OK) {
        char msg[256];
        snprintf(msg, sizeof msg, "sqlite3: %s", sqlite3_errmsg(db));
        sqlite3_finalize(stmt);
        ps_raise_cstr(PS_RUNTIMEERROR, msg);
    }
    for (; tail != NULL && tail < sql + n; tail++) {
        if (*tail != ' ' && *tail != '\t' && *tail != '\n' && *tail != '\r' && *tail != ';') {
            sqlite3_finalize(stmt);
            ps_raise_cstr(PS_RUNTIMEERROR, "sqlite3: You can only execute one statement at a time.");
        }
    }
    PsSeq *rows = ps_seq_new((int64_t)sizeof(PsMap *), 0);
    if (stmt == NULL) {
        return rows; /* empty or comment-only SQL */
    }
    int ncols = sqlite3_column_count(stmt);
    while ((rc = sqlite3_step(stmt)) == SQLITE_ROW) {
        PsMap *row = ps_map_new(2, (int64_t)sizeof(PsBuf), (int64_t)sizeof(PsBuf), ncols);
        for (int i = 0; i < ncols; i++) {
            PsBuf key;
            PsBuf val;
            const char *name = sqlite3_column_name(stmt, i);
            const unsigned char *text = sqlite3_column_text(stmt, i);
            ps_buf_copy(&key, name != NULL ? name : "", name != NULL ? (int64_t)strlen(name) : 0);
            ps_buf_copy(&val, text != NULL ? (const char *)text : "", (int64_t)sqlite3_column_bytes(stmt, i));
            ps_map_put(row, &key, &val);
        }
        ps_seq_push(rows, &row);
    }
    if (rc != SQLITE_DONE) {
        char msg[256];
        snprintf(msg, sizeof msg, "sqlite3: %s", sqlite3_errmsg(db));
        sqlite3_finalize(stmt);
        ps_raise_cstr(PS_RUNTIMEERROR, msg);
    }
    sqlite3_finalize(stmt);
    return rows;
}

int64_t ps_ffi_sqlite_commit(void *handle) {
    sqlite3 *db = ps_sqlite_live(handle);
    if (sqlite3_get_autocommit(db)) {
        return SQLITE_OK; /* no open transaction: nothing to commit, like sqlite3 */
    }
    char *err = NULL;
    int rc = sqlite3_exec(db, "COMMIT", NULL, NULL, &err);
    if (rc != SQLITE_OK) {
        char msg[256];
        snprintf(msg, sizeof msg, "sqlite3: %s", err != NULL ? err : sqlite3_errstr(rc));
        sqlite3_free(err);
        ps_raise_cstr(PS_RUNTIMEERROR, msg);
    }
    return SQLITE_OK;
}

void ps_ffi_sqlite_close(void *handle) {
    ps_sqlite_conn *conn = handle;
    if (conn == NULL || conn->db == NULL) {
        return; /* closing twice is a no-op */
    }
    sqlite3 *db = conn->db;
    conn->db = NULL;
    sqlite3_close_v2(db);
}

/* The v2 surface (issue #1343). */

/* The tagged cell a bound parameter or a returned column carries. The cell is
   the runtime's own PsDyn, so the compiler's `object` slot (a pointer to the
   cell) and the C side agree on one representation. */
static PsDyn *ps_sqlite_cell_of(const void *slot) {
    const PsDyn *cell = *(const PsDyn *const *)slot;
    return (PsDyn *)cell;
}

/* Bind one tagged cell to the next `?` placeholder. The arm names the SQLite
   storage class: an int and a float are numbers, a str is text, the none arm
   is a SQL NULL. A bool cell binds as an integer, the way sqlite3 does. */
static void ps_sqlite_bind(sqlite3_stmt *stmt, int index, const PsDyn *cell) {
    if (cell == NULL || cell->tag == PS_DYN_NONE) {
        sqlite3_bind_null(stmt, index);
        return;
    }
    switch (cell->tag) {
    case PS_DYN_INT:
        sqlite3_bind_int64(stmt, index, (sqlite3_int64)cell->i);
        return;
    case PS_DYN_BOOL:
        sqlite3_bind_int64(stmt, index, (sqlite3_int64)(cell->i ? 1 : 0));
        return;
    case PS_DYN_FLOAT: {
        double value;
        memcpy(&value, &cell->i, sizeof value);
        sqlite3_bind_double(stmt, index, value);
        return;
    }
    case PS_DYN_STR:
        sqlite3_bind_text(stmt, index, cell->b.data, (int)cell->b.len, SQLITE_TRANSIENT);
        return;
    default:
        /* A list or a dict cell has no SQLite storage class; the compiler
           refuses those arms before the call, so this is unreachable. */
        sqlite3_bind_null(stmt, index);
        return;
    }
}

/* Bind the whole parameter list to the statement's placeholders. The list is
   the compiler's `list[object]`: a PsSeq whose elements are pointers to the
   tagged cells. */
static void ps_sqlite_bind_all(sqlite3_stmt *stmt, const PsSeq *params) {
    if (params == NULL) {
        return;
    }
    int64_t count = ps_seq_len((PsSeq *)params);
    for (int64_t i = 0; i < count; i++) {
        PsDyn *cell = NULL;
        ps_seq_get((PsSeq *)params, i, &cell);
        ps_sqlite_bind(stmt, (int)(i + 1), cell);
    }
}

/* The tagged cell a column value becomes. The storage class decides the arm:
   sqlite3_column_type reports INTEGER, FLOAT, TEXT, BLOB or NULL. */
static PsDyn *ps_sqlite_cell_of_column(sqlite3_stmt *stmt, int index) {
    switch (sqlite3_column_type(stmt, index)) {
    case SQLITE_INTEGER:
        return ps_dyn_new_int((int64_t)sqlite3_column_int64(stmt, index));
    case SQLITE_FLOAT: {
        double value = sqlite3_column_double(stmt, index);
        return ps_dyn_new_float(value);
    }
    case SQLITE_TEXT:
    case SQLITE_BLOB: {
        const unsigned char *text = sqlite3_column_text(stmt, index);
        int64_t bytes = (int64_t)sqlite3_column_bytes(stmt, index);
        PsBuf buf;
        ps_buf_copy(&buf, text != NULL ? (const char *)text : "", bytes);
        return ps_dyn_new_str(buf.len, buf.data);
    }
    default:
        return ps_dyn_new_none();
    }
}

/* Prepare the statement and refuse the multi-statement SQL the way the v1
   surface does. The caller owns the prepared statement. */
static sqlite3_stmt *ps_sqlite_prepare(sqlite3 *db, int64_t n, const char *sql) {
    sqlite3_stmt *stmt = NULL;
    const char *tail = NULL;
    int rc = sqlite3_prepare_v2(db, sql, (int)n, &stmt, &tail);
    if (rc != SQLITE_OK) {
        char msg[256];
        snprintf(msg, sizeof msg, "sqlite3: %s", sqlite3_errmsg(db));
        sqlite3_finalize(stmt);
        ps_raise_cstr(PS_RUNTIMEERROR, msg);
    }
    for (; tail != NULL && tail < sql + n; tail++) {
        if (*tail != ' ' && *tail != '\t' && *tail != '\n' && *tail != '\r' && *tail != ';') {
            sqlite3_finalize(stmt);
            ps_raise_cstr(PS_RUNTIMEERROR, "sqlite3: You can only execute one statement at a time.");
        }
    }
    return stmt;
}

/* execute(db, sql, params) -> int: the statement's row count. A write
   reports the rows it changed, a read reports -1 like sqlite3 does. */
int64_t ps_ffi_sqlite_execute2(void *handle, int64_t n, const char *sql, void *params) {
    sqlite3 *db = ps_sqlite_live(handle);
    sqlite3_stmt *stmt = ps_sqlite_prepare(db, n, sql);
    if (stmt == NULL) {
        return 0; /* empty or comment-only SQL */
    }
    ps_sqlite_bind_all(stmt, (const PsSeq *)params);
    int rc = sqlite3_step(stmt);
    if (rc != SQLITE_DONE && rc != SQLITE_ROW) {
        char msg[256];
        snprintf(msg, sizeof msg, "sqlite3: %s", sqlite3_errmsg(db));
        sqlite3_finalize(stmt);
        ps_raise_cstr(PS_RUNTIMEERROR, msg);
    }
    int64_t changes = (int64_t)sqlite3_changes(db);
    sqlite3_finalize(stmt);
    return changes;
}

/* executemany(db, sql, rows) -> int: the statement run once per row of the
   parameter list. The rows list is a list of lists, so each inner list is a
   PsSeq of tagged cells. The count is the rows of the last run, like
   sqlite3's cursor.rowcount after executemany. */
int64_t ps_ffi_sqlite_executemany(void *handle, int64_t n, const char *sql, void *rows) {
    sqlite3 *db = ps_sqlite_live(handle);
    PsSeq *seq = (PsSeq *)rows;
    int64_t count = ps_seq_len(seq);
    if (count == 0) {
        return 0;
    }
    sqlite3_stmt *stmt = ps_sqlite_prepare(db, n, sql);
    if (stmt == NULL) {
        return 0;
    }
    int64_t changes = 0;
    for (int64_t i = 0; i < count; i++) {
        PsSeq *row = NULL;
        ps_seq_get(seq, i, &row);
        sqlite3_reset(stmt);
        sqlite3_clear_bindings(stmt);
        ps_sqlite_bind_all(stmt, row);
        int rc = sqlite3_step(stmt);
        if (rc != SQLITE_DONE && rc != SQLITE_ROW) {
            char msg[256];
            snprintf(msg, sizeof msg, "sqlite3: %s", sqlite3_errmsg(db));
            sqlite3_finalize(stmt);
            ps_raise_cstr(PS_RUNTIMEERROR, msg);
        }
        changes = (int64_t)sqlite3_changes(db);
    }
    sqlite3_finalize(stmt);
    return changes;
}

/* query(db, sql, params) -> list[dict[str, object]]: the statement's rows,
   each a map from the column name to the column's tagged value. The map
   keeps the column order, so its keys are the statement's column list. */
PsSeq *ps_ffi_sqlite_query2(void *handle, int64_t n, const char *sql, void *params) {
    sqlite3 *db = ps_sqlite_live(handle);
    sqlite3_stmt *stmt = ps_sqlite_prepare(db, n, sql);
    PsSeq *rows = ps_seq_new((int64_t)sizeof(PsMap *), 0);
    if (stmt == NULL) {
        return rows;
    }
    ps_sqlite_bind_all(stmt, (const PsSeq *)params);
    int ncols = sqlite3_column_count(stmt);
    int rc;
    while ((rc = sqlite3_step(stmt)) == SQLITE_ROW) {
        PsMap *row = ps_map_new(2, (int64_t)sizeof(PsBuf), (int64_t)sizeof(void *), ncols);
        for (int i = 0; i < ncols; i++) {
            PsBuf key;
            const char *name = sqlite3_column_name(stmt, i);
            ps_buf_copy(&key, name != NULL ? name : "", name != NULL ? (int64_t)strlen(name) : 0);
            PsDyn *cell = ps_sqlite_cell_of_column(stmt, i);
            ps_map_put(row, &key, &cell);
        }
        ps_seq_push(rows, &row);
    }
    if (rc != SQLITE_DONE) {
        char msg[256];
        snprintf(msg, sizeof msg, "sqlite3: %s", sqlite3_errmsg(db));
        sqlite3_finalize(stmt);
        ps_raise_cstr(PS_RUNTIMEERROR, msg);
    }
    sqlite3_finalize(stmt);
    return rows;
}

/* lastrowid(db) -> int: the rowid of the connection's last insert. */
int64_t ps_ffi_sqlite_lastrowid(void *handle) {
    sqlite3 *db = ps_sqlite_live(handle);
    return (int64_t)sqlite3_last_insert_rowid(db);
}

/* connect(path, timeout_s) -> Connection: the open with the busy timeout the
   caller asked for. CPython's `timeout` is in seconds and defaults to 5.0;
   the adapter takes it as an explicit argument so the subset's call site
   spells it. */
void *ps_ffi_sqlite_connect2(int64_t n, const char *path, double timeout_s) {
    void *handle = ps_ffi_sqlite_connect(n, path);
    sqlite3 *db = ps_sqlite_live((ps_sqlite_conn *)handle);
    int ms = (int)(timeout_s * 1000.0);
    if (ms < 0) {
        ms = 0;
    }
    sqlite3_busy_timeout(db, ms);
    return handle;
}

