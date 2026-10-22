/* pyscriptc-sqlite thunk (.agents/docs/ffi/ffi-manifest.md section 5).

The manifest binds `abi = ps_ffi_sqlite_*` here, never the SQLite symbols. This
file owns everything the ABI cannot express: the variadic-free open flags,
out-params, NUL termination, error mapping and the construction of result rows
behind the ps_rt.h layout (PsSeq/PsMap, ps_alloc). Callers see a Connection as
an opaque pointer; no field crosses the boundary.

Strings arrive as (i64 len, ptr) pairs and are not NUL terminated.

Exception surface (mirrored in pyscriptc_sqlite.pyi):
  PS_RUNTIMEERROR  SQLite error or more than one statement; "sqlite3: <text>"
  PS_VALUEERROR    the connection is closed
Rows are list[dict[str, str]]: every column is rendered with sqlite3_column_text
and SQL NULL becomes the empty string. */

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
