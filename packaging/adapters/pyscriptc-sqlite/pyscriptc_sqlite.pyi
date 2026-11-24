"""The pyscriptc-sqlite surface: the sqlite3 connection/cursor shape (#1343).

The manifest claims the stdlib module name `sqlite3`, so `import sqlite3`
lowers through this surface; `import pyscriptc_sqlite` is the explicit
spelling of the same manifest and the same thunks.

The connection/cursor shape (issue #1343):

- `connect(path)` opens the database (the busy timeout rides the open's
  default, 5 seconds, CPython's `timeout` default).
- `db.execute(sql, params)` prepares the statement, binds the params and
  returns the cursor. A DML statement runs to completion at the call, so
  `cur.rowcount` is decided when `execute` returns; a SELECT keeps its
  rows on the cursor for the fetch methods. `params` may be omitted: an
  empty bind.
- `db.executemany(sql, rows)` runs the statement once per params list;
  `cur.rowcount` is the sum of the runs' changed rows, CPython's rule.
- `db.commit()` commits, `db.close()` closes (and consumes the handle,
  so a later use is the PS4010 compile fence).
- `cur.fetchone()` returns the next row or None; `cur.fetchall()`
  returns the remaining rows. The row is positional, CPython's tuple
  shape: `row[i]` is the column value in column order.
- `cur.rowcount` is the row count of the last run: -1 for a read, the
  changed rows for a write, the sum for an executemany.
- `cur.lastrowid` is the rowid of the last insert (0 before any insert).
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

Divergences from CPython (docs/reference/divergences.md, entry 66): the
row is a list, not a tuple (printing it is the list spelling); the
description element is the bare name, not the 7-tuple; there is no
context manager, no timeout/isolation_level arguments spelled at the
call, no row_factory, and no conn.execute PRAGMA row rows beyond the
count.
"""

OK: int
ROW: int
DONE: int


class Connection: ...


class Cursor: ...


def connect(path: str) -> Connection: ...
def close(db: Connection) -> None: ...
