/* Host harness for the pyscriptc-sqlite thunk: runs the sequence the CPython
   differential test also runs and prints rows in one canonical form. Linked
   with the runtime C files (no island), the thunk and sqlite3.c. */

#include <stdio.h>

#include "ps_rt.h"

void *ps_ffi_sqlite_connect(int64_t n, const char *path);
int64_t ps_ffi_sqlite_execute(void *db, int64_t n, const char *sql);
PsSeq *ps_ffi_sqlite_query(void *db, int64_t n, const char *sql);
int64_t ps_ffi_sqlite_commit(void *db);
void ps_ffi_sqlite_close(void *db);

#define S(lit) (int64_t)(sizeof(lit) - 1), lit

static void print_rows(PsSeq *rows) {
    for (int64_t i = 0; i < ps_seq_len(rows); i++) {
        PsMap *row;
        ps_seq_get(rows, i, &row);
        printf("row");
        for (int64_t j = 0; j < ps_map_len(row); j++) {
            PsBuf k;
            PsBuf v;
            ps_map_key_at(row, j, &k);
            ps_map_val_at(row, j, &v);
            printf(" %.*s=%.*s", (int)k.len, k.data, (int)v.len, v.data);
        }
        printf("\n");
    }
}

typedef struct Env {
    void *db;
    const char *what;
} Env;

static int32_t run_bad_sql(void *p) {
    Env *e = p;
    ps_ffi_sqlite_query(e->db, S("SELEKT 1"));
    return 0;
}

static int32_t run_exec_after_close(void *p) {
    Env *e = p;
    ps_ffi_sqlite_execute(e->db, S("SELECT 1"));
    return 0;
}

static int32_t run_two_statements(void *p) {
    Env *e = p;
    ps_ffi_sqlite_query(e->db, S("SELECT 1; SELECT 2"));
    return 0;
}

typedef int32_t (*fn_t)(void *);
int32_t ps_try_call(fn_t fn, void *env, int32_t *flow);

static void expect_raise(const char *label, fn_t fn, void *db) {
    Env env = {db, label};
    int32_t flow = 0;
    int32_t code = ps_try_call(fn, &env, &flow);
    if (code == 0) {
        printf("%s: no error\n", label);
        return;
    }
    PsExc *e = ps_exc_current();
    printf("%s: code=%d msg=%.*s\n", label, e->code, (int)e->message.len, e->message.data);
}

int main(void) {
    void *db = ps_ffi_sqlite_connect(S(":memory:"));
    ps_ffi_sqlite_execute(db, S("CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT, score REAL, note TEXT)"));
    ps_ffi_sqlite_execute(db, S("INSERT INTO t (name, score, note) VALUES ('ada', 1.5, NULL), ('bob', 2, 'x y'), ('caf\xc3\xa9', -3.25, '')"));
    print_rows(ps_ffi_sqlite_query(db, S("SELECT id, name, score, note FROM t ORDER BY id")));
    print_rows(ps_ffi_sqlite_query(db, S("SELECT count(*) AS n FROM t WHERE score > 0")));
    print_rows(ps_ffi_sqlite_query(db, S("UPDATE t SET score = 0 WHERE id = 1")));
    ps_ffi_sqlite_execute(db, S("BEGIN"));
    ps_ffi_sqlite_execute(db, S("DELETE FROM t WHERE id = 2"));
    printf("commit=%lld\n", (long long)ps_ffi_sqlite_commit(db));
    printf("commit_idle=%lld\n", (long long)ps_ffi_sqlite_commit(db));
    print_rows(ps_ffi_sqlite_query(db, S("SELECT id FROM t ORDER BY id")));
    expect_raise("bad_sql", run_bad_sql, db);
    expect_raise("two_statements", run_two_statements, db);
    ps_ffi_sqlite_close(db);
    ps_ffi_sqlite_close(db);
    expect_raise("closed", run_exec_after_close, db);
    return 0;
}
