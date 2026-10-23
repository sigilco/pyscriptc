import pyscriptc_sqlite


def main() -> None:
    db: pyscriptc_sqlite.Connection = pyscriptc_sqlite.connect(":memory:")
    pyscriptc_sqlite.execute(db, "CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT)")
    pyscriptc_sqlite.execute(db, "INSERT INTO t (name) VALUES ('ada'), ('bo')")
    rows: list[dict[str, str]] = pyscriptc_sqlite.query(db, "SELECT id, name FROM t ORDER BY id")
    for row in rows:
        print(row["name"])
    pyscriptc_sqlite.close(db)
