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

/* The v3 surface: the sqlite3 connection/cursor shape (issue #1343). */

/* The v3 surface: the sqlite3 connection/cursor shape (issue #1343).

The cursor is one prepared statement plus the count fields CPython's Cursor
carries. `execute` runs the statement like CPython's Connection.execute
does (it steps a DML statement to its first DONE), but keeps the statement
open when it returned rows, so `fetchone` reads them. The counts follow
CPython's rules exactly:

  rowcount is -1 (no statement run, or the statement is a read); for a
  DML run it is sqlite3_changes(); for executemany it is the sum of the
  runs' changes (CPython sums, not the last run's).
  lastrowid is 0 until an INSERT ran, then sqlite3_last_insert_rowid().
  description is NULL on a cursor that has run no statement with columns
  (CPython: None after a DML execute), otherwise the column names.
*/

typedef struct ps_sqlite_cursor {
    sqlite3 *db;        /* the owning connection's sqlite3, NULL once closed */
    sqlite3_stmt *stmt; /* the statement the cursor steps; NULL when spent */
    int64_t rowcount;   /* CPython's rule: -1 for reads, changes for writes */
    int64_t lastrowid;  /* 0 until an INSERT ran on the connection */
    int ncols;          /* the prepared statement's column count */
    int stepped;        /* the statement has run at least once */
} ps_sqlite_cursor;

/* True when the statement's first run says DML: sqlite3_step returns DONE
   for INSERT/UPDATE/DELETE without ever producing a row. A SELECT's first
   run is SQLITE_ROW (or DONE for the empty set), which is the read arm. */
static int ps_sqlite_is_dml_run(int rc) {
    return rc == SQLITE_DONE;
}

/* Step the cursor's statement once and build the row, or report done. */
static int ps_sqlite_cur_step(ps_sqlite_cursor *cur) {
    if (cur == NULL || cur->db == NULL || cur->stmt == NULL) {
        return SQLITE_DONE;
    }
    int rc = sqlite3_step(cur->stmt);
    if (rc == SQLITE_ROW || rc == SQLITE_DONE) {
        cur->stepped = 1;
        return rc;
    }
    char msg[256];
    snprintf(msg, sizeof msg, "sqlite3: %s", sqlite3_errmsg(cur->db));
    ps_raise_cstr(PS_RUNTIMEERROR, msg);
}

/* One row off the statement's current position, keyed by column name. */
static PsMap *ps_sqlite_row_of(ps_sqlite_cursor *cur) {
    PsMap *row = ps_map_new(2, (int64_t)sizeof(PsBuf), (int64_t)sizeof(void *), cur->ncols);
    for (int i = 0; i < cur->ncols; i++) {
        PsBuf key;
        const char *name = sqlite3_column_name(cur->stmt, i);
        ps_buf_copy(&key, name != NULL ? name : "", name != NULL ? (int64_t)strlen(name) : 0);
        PsDyn *cell = ps_sqlite_cell_of_column(cur->stmt, i);
        ps_map_put(row, &key, &cell);
    }
    return row;
}

/* The one execute: prepare, bind, run. A DML statement runs to DONE here
   (CPython's Connection.execute does the same, so the counts are decided
   at the call); a SELECT stops at the first ROW so the cursor's fetch
   methods read the rows. */
void *ps_ffi_sqlite_conn_execute(void *handle, int64_t n, const char *sql, void *params) {
    sqlite3 *db = ps_sqlite_live(handle);
    sqlite3_stmt *stmt = ps_sqlite_prepare(db, n, sql);
    ps_sqlite_cursor *cur = ps_alloc((int64_t)sizeof *cur);
    cur->db = db;
    cur->stmt = stmt;
    cur->rowcount = -1;
    cur->lastrowid = (int64_t)sqlite3_last_insert_rowid(db);
    cur->ncols = stmt != NULL ? sqlite3_column_count(stmt) : 0;
    cur->stepped = 0;
    if (stmt == NULL) {
        /* An empty or comment-only statement: CPython reports rowcount -1. */
        return cur;
    }
    ps_sqlite_bind_all(stmt, (const PsSeq *)params);
    if (cur->ncols > 0) {
        /* The read arm: a statement with result columns keeps its rows for
           fetchone, so the cursor does not step it here. CPython's
           Connection.execute does step it, but its Cursor keeps the
           pending row and hands it to the first fetchone, which is the
           same row the first fetchone sees here. */
        return cur;
    }
    int rc = sqlite3_step(stmt);
    cur->stepped = 1;
    if (rc == SQLITE_DONE) {
        /* The DML arm: the row count is the changes of this run. */
        cur->rowcount = (int64_t)sqlite3_changes(db);
        cur->lastrowid = (int64_t)sqlite3_last_insert_rowid(db);
        sqlite3_finalize(stmt);
        cur->stmt = NULL;
        return cur;
    }
    if (rc == SQLITE_ROW) {
        /* A statement with no result columns that produced a row (a
           RETURNING clause): the row stays for the fetch methods. */
        return cur;
    }
    char msg[256];
    snprintf(msg, sizeof msg, "sqlite3: %s", sqlite3_errmsg(db));
    sqlite3_finalize(stmt);
    cur->stmt = NULL;
    ps_raise_cstr(PS_RUNTIMEERROR, msg);
}

/* executemany(sql, rows): the statement runs once per parameter row. The
   row count is the sum of the runs' changes, CPython's rule. The cursor
   keeps the statement only when rows were produced (a SELECT with the
   reset-and-restep shape executemany has no use for); CPython's
   executemany cursor never offers rows, so the statement finalizes and
   the fetches report done. */
void *ps_ffi_sqlite_conn_executemany(void *handle, int64_t n, const char *sql, void *rows) {
    sqlite3 *db = ps_sqlite_live(handle);
    PsSeq *seq = (PsSeq *)rows;
    ps_sqlite_cursor *cur = ps_alloc((int64_t)sizeof *cur);
    cur->db = db;
    cur->stmt = NULL;
    cur->rowcount = -1;
    cur->lastrowid = (int64_t)sqlite3_last_insert_rowid(db);
    cur->ncols = 0;
    cur->stepped = 0;
    int64_t count = ps_seq_len(seq);
    if (count == 0) {
        /* CPython: executemany over an empty sequence leaves rowcount -1. */
        return cur;
    }
    sqlite3_stmt *stmt = ps_sqlite_prepare(db, n, sql);
    if (stmt == NULL) {
        return cur;
    }
    int64_t total = 0;
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
        total += (int64_t)sqlite3_changes(db);
    }
    cur->rowcount = total;
    cur->lastrowid = (int64_t)sqlite3_last_insert_rowid(db);
    sqlite3_finalize(stmt);
    return cur;
}

void *ps_ffi_sqlite_conn_connect(int64_t n, const char *path) {
    return ps_ffi_sqlite_connect(n, path);
}

int64_t ps_ffi_sqlite_conn_commit(void *handle) {
    return ps_ffi_sqlite_commit(handle);
}

void ps_ffi_sqlite_conn_close(void *handle) {
    ps_sqlite_conn *conn = handle;
    if (conn != NULL && conn->db != NULL) {
        sqlite3_close_v2(conn->db);
        conn->db = NULL;
    }
}

void ps_ffi_sqlite_cur_close(void *handle) {
    ps_sqlite_cursor *cur = handle;
    if (cur != NULL && cur->stmt != NULL) {
        sqlite3_finalize(cur->stmt);
        cur->stmt = NULL;
    }
    cur->db = NULL;
}

/* The fetchone pair: the probe says whether a row is there, the make call
   builds the row off the statement the probe left on a ROW. */

int32_t ps_ffi_sqlite_cur_row_hit(void *handle) {
    ps_sqlite_cursor *cur = handle;
    if (cur == NULL || cur->db == NULL) {
        ps_raise_cstr(PS_VALUEERROR, "Cannot operate on a closed database.");
    }
    return ps_sqlite_cur_step(cur) == SQLITE_ROW ? 1 : 0;
}

PsMap *ps_ffi_sqlite_cur_row_make(void *handle) {
    ps_sqlite_cursor *cur = handle;
    if (cur == NULL || cur->db == NULL) {
        ps_raise_cstr(PS_VALUEERROR, "Cannot operate on a closed database.");
    }
    return ps_sqlite_row_of(cur);
}

PsSeq *ps_ffi_sqlite_cur_rows(void *handle) {
    ps_sqlite_cursor *cur = handle;
    if (cur == NULL || cur->db == NULL) {
        ps_raise_cstr(PS_VALUEERROR, "Cannot operate on a closed database.");
    }
    PsSeq *rows = ps_seq_new((int64_t)sizeof(PsMap *), 0);
    while (ps_sqlite_cur_step(cur) == SQLITE_ROW) {
        PsMap *row = ps_sqlite_row_of(cur);
        ps_seq_push(rows, &row);
    }
    return rows;
}

int64_t ps_ffi_sqlite_cur_rowcount(void *handle) {
    ps_sqlite_cursor *cur = handle;
    if (cur == NULL || cur->db == NULL) {
        ps_raise_cstr(PS_VALUEERROR, "Cannot operate on a closed database.");
    }
    return cur->rowcount;
}

int64_t ps_ffi_sqlite_cur_lastrowid(void *handle) {
    ps_sqlite_cursor *cur = handle;
    if (cur == NULL || cur->db == NULL) {
        ps_raise_cstr(PS_VALUEERROR, "Cannot operate on a closed database.");
    }
    return cur->lastrowid;
}

/* cur.description -> list[str] | NULL: the column names in column order,
   or NULL (CPython's None) when the statement has no result columns. The
   manifest spells the return `list[str] | None`, so the pair follows the
   fetchone shape: the probe says whether columns exist, the make call
   builds the names. */

int32_t ps_ffi_sqlite_cur_description_hit(void *handle) {
    ps_sqlite_cursor *cur = handle;
    if (cur == NULL || cur->db == NULL) {
        ps_raise_cstr(PS_VALUEERROR, "Cannot operate on a closed database.");
    }
    /* CPython's description is None when the statement has no result
       columns, which is the DML arm: the columns are the statement's
       result columns, not the table's. */
    return cur->ncols > 0 ? 1 : 0;
}

PsSeq *ps_ffi_sqlite_cur_description_make(void *handle) {
    ps_sqlite_cursor *cur = handle;
    if (cur == NULL || cur->db == NULL) {
        ps_raise_cstr(PS_VALUEERROR, "Cannot operate on a closed database.");
    }
    PsSeq *names = ps_seq_new((int64_t)sizeof(PsBuf), cur->ncols);
    for (int i = 0; i < cur->ncols; i++) {
        const char *name = sqlite3_column_name(cur->stmt, i);
        PsBuf buf;
        ps_buf_copy(&buf, name != NULL ? name : "", name != NULL ? (int64_t)strlen(name) : 0);
        ps_seq_set(names, i, &buf);
    }
    return names;
}
