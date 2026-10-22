"""The pyscriptc-sqlite surface: connect, execute, query, commit, close.

Positional arguments only, no cursors, no callbacks, no context managers.
The connection is autocommit: every statement commits on its own, so commit()
only matters after an explicit BEGIN.

Exception surface:
- RuntimeError: SQLite refused the statement (bad SQL, constraint failure,
  unopenable file) or the SQL holds more than one statement. The message is
  "sqlite3: " plus SQLite's own error text.
- ValueError: the connection is already closed. close() on a closed
  connection is a no-op, like sqlite3.Connection.close().
"""

class Connection: ...

OK: int
ROW: int
DONE: int

def connect(path: str) -> Connection: ...
def execute(db: Connection, sql: str) -> int: ...
def query(db: Connection, sql: str) -> list: ...
def commit(db: Connection) -> int: ...
def close(db: Connection) -> None: ...
