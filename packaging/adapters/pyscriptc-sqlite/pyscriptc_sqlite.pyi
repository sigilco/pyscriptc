"""The pyscriptc-sqlite surface: the sqlite3 connection/cursor shape (#1343).

The manifest claims the stdlib module name `sqlite3`, so `import sqlite3`
lowers through this surface; `import pyscriptc_sqlite` is the explicit
spelling of the same manifest and the same thunks.

The connection/cursor shape (issue #1343):

- `connect(path)` opens the database (the busy timeout rides the open's
  default, 5 seconds, CPython's `timeout` default).
- `db.execute(sql, params)` prepares the statement, binds the params,
  steps it once and returns the cursor, CPython's `cursor.execute`. The
  step at the call settles a DML run's row count and parks a read's first
  row for the first `fetchone`. `params` may be omitted: an empty bind.
- `db.executemany(sql, rows)` runs the statement once per params list;
  `cur.rowcount` is the sum of the runs' changed rows, CPython's rule.
  A statement that is not an INSERT, UPDATE, DELETE or REPLACE is
  refused with RuntimeError, CPython's ProgrammingError.
- `db.commit()` commits, `db.close()` closes (and consumes the handle,
  so a later use is the PS4010 compile fence).
- `cur.fetchone()` returns the next row or None; `cur.fetchall()`
  returns the remaining rows. The row is positional, CPython's tuple
  shape: `row[i]` is the column value in column order.
- `cur.rowcount` follows CPython's rule: -1 for a statement that is not
  an INSERT, UPDATE, DELETE or REPLACE, the changed rows for a DML run
  and the sum for an executemany.
- `cur.lastrowid` is the rowid of the last insert (0 before any insert).
  CPython's None on a cursor no `execute` ran on is 0 here.
- `cur.description` is the list of column names, or None when the
  statement has no result columns (the DML arm, CPython's None).

A bound parameter and a returned column ride the tagged dynamic value:
an int, a float, a str, a bool or None. None binds a SQL NULL; a NULL
column reads back the None arm, and `value is None` is the runtime's tag
test. `isinstance(value, int)` / float / str narrows the cell inside the
taken branch.

Exception surface:
- RuntimeError: SQLite refused the statement (bad SQL, constraint
  failure, unopenable file) or the SQL holds more than one statement.
  The message is "sqlite3: " plus SQLite's own error text.
- ValueError: the connection is already closed.

Divergences from CPython (docs/reference/divergences.md, entry 92): the
row is a list, not a tuple (printing it is the list spelling); the
description element is the bare name, not the 7-tuple; `cur.lastrowid` is
0 where CPython answers None; there is no context manager, no
timeout/isolation_level arguments spelled at the call, no row_factory,
and no conn.execute PRAGMA row rows beyond the
count.
"""

OK: int
ROW: int
DONE: int

class Connection:
    def execute(self, sql: str, params: list[object] = ...) -> Cursor: ...
    def executemany(self, sql: str, rows: list[list[object]]) -> Cursor: ...
    def commit(self) -> int: ...
    def close(self) -> None: ...

class Cursor:
    def fetchone(self) -> list[object] | None: ...
    def fetchall(self) -> list[list[object]]: ...
    def close(self) -> None: ...
    @property
    def rowcount(self) -> int: ...
    @property
    def lastrowid(self) -> int: ...
    @property
    def description(self) -> list[str] | None: ...

def connect(path: str, timeout: float = 5.0, isolation_level: object = None) -> Connection: ...
def close(db: Connection) -> None: ...
