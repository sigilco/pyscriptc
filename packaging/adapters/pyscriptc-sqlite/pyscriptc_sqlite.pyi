"""The pyscriptc-sqlite surface.

v1 (the pilot): connect, execute, query, commit, close. Positional
arguments only, no cursors, no callbacks, no context managers. The
connection is autocommit: every statement commits on its own, so commit()
only matters after an explicit BEGIN.

v2 (issue #1343): parameter binding and typed rows.

- `execute2(db, sql, params)` binds the params list to the statement's `?`
  placeholders in order. Each element is an `object`: an int, a float, a
  str, a bool or None. None binds a SQL NULL; a bool binds 0 or 1. The
  return is the statement's row count (sqlite3's cursor.rowcount for a
  write).
- `executemany(db, sql, rows)` runs the statement once per inner params
  list and returns the last run's row count.
- `query2(db, sql, params)` binds like execute2 and returns the rows as
  `list[dict[str, object]]`: the column name is the key, the column value
  keeps its own type (an INTEGER column reads back an int, a REAL a
  float, TEXT a str, a SQL NULL the None arm). The dict preserves the
  column order, so `row.keys()` is the statement's column list.
- `lastrowid(db)` is the rowid of the connection's last insert.
- `connect2(path, timeout)` opens with a busy timeout in seconds, the
  spelling of CPython's `sqlite3.connect(timeout=...)` default of 5.0.

Narrowing a typed cell: `isinstance(v, int)` / float / str inside the
taken branch gives the native value; the branch none of the arms took is
the NULL cell. The JSON keys use (`cli.py` reading `cur.description`)
read the dict's keys, which are the column names in order.

Exception surface:
- RuntimeError: SQLite refused the statement (bad SQL, constraint failure,
  unopenable file) or the SQL holds more than one statement. The message is
  "sqlite3: " plus SQLite's own error text.
- ValueError: the connection is already closed. close() on a closed
  connection is a no-op, like sqlite3.Connection.close().

Divergence from CPython (docs/reference/divergences.md, entry 66): the
surface is module-level functions over one connection handle, not the
Connection/Cursor class pair; there is no rowcount/lastrowid/description
attribute and no context manager, the row is a dict rather than a tuple,
and float rows print without CPython's repr rounding.
"""

class Connection: ...

OK: int
ROW: int
DONE: int

def connect(path: str) -> Connection: ...
def execute(db: Connection, sql: str) -> int: ...
def query(db: Connection, sql: str) -> list[dict[str, str]]: ...
def commit(db: Connection) -> int: ...
def close(db: Connection) -> None: ...

def connect2(path: str, timeout: float) -> Connection: ...
def execute2(db: Connection, sql: str, params: list[object]) -> int: ...
def executemany(db: Connection, sql: str, rows: list[list[object]]) -> int: ...
def query2(db: Connection, sql: str, params: list[object]) -> list[dict[str, object]]: ...
def lastrowid(db: Connection) -> int: ...
