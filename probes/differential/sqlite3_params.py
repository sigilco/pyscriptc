# SPDX-License-Identifier: Apache-2.0
"""The sqlite3 subset over `?` parameters, typed rows, rowcount and lastrowid.

The AGENTS.md differential-probe row for issue #1343. The same source
runs on the pinned CPython 3.14 (where `import sqlite3` is the stdlib
module) and on the compiled binary (where `import sqlite3` is the
adapter manifest's surface), and the two byte-compare on stdout, stderr
and the exit code.

The surface under the probe:

  - a `?` parameter bound to an int, a float, a str and a None;
  - the NULL round trip (a None parameter comes back as None);
  - `cur.rowcount` (the changed rows of a write, -1 for a read);
  - `cur.lastrowid` (the rowid of the last insert);
  - `cur.description`'s None-or-names shape and its column count;
  - the `connect` keywords (`timeout`, `isolation_level=None`).

The row is positional, CPython's tuple shape: the value is `row[i]` and
the column's name rides `cur.description`. A None cell prints as None on
both sides, so the NULL round trip byte-compares. The description's
element is the bare column name, where CPython's is a 7-tuple, so the
probe prints the description's presence and its length and leaves the
element shape to the divergence entry.
"""

import sqlite3


def main() -> None:
    db = sqlite3.connect(":memory:", timeout=2.0, isolation_level=None)
    db.execute("CREATE TABLE job (id INTEGER PRIMARY KEY, name TEXT, tries INTEGER, note TEXT)")

    cur = db.execute("INSERT INTO job (name, tries, note) VALUES (?, ?, ?)", ["alpha", 3, None])
    print("rowcount", cur.rowcount)
    print("lastrowid", cur.lastrowid)

    db.executemany(
        "INSERT INTO job (name, tries, note) VALUES (?, ?, ?)",
        [["beta", 0, "first"], ["gamma", 7, None]],
    )

    cur = db.execute("SELECT id, name, tries, note FROM job ORDER BY id")
    print("select rowcount", cur.rowcount)
    while True:
        row = cur.fetchone()
        if row is None:
            break
        print("row", row[0], row[1], row[2], row[3])

    cur = db.execute("SELECT id, name, tries, note FROM job WHERE name = ? AND note = ?", ["gamma", None])
    row = cur.fetchone()
    if row is not None:
        print("bound", row[0], row[1], row[2], row[3])
        print("null is none", row[3] is None)

    cur = db.execute("SELECT id, name FROM job ORDER BY id")
    for each in cur.fetchall():
        print("all", each[0], each[1])

    # The float arm: a REAL column reads back as a float.
    db.execute("CREATE TABLE reading (id INTEGER PRIMARY KEY, value REAL)")
    db.execute("INSERT INTO reading (value) VALUES (?)", [1.5])
    cur = db.execute("SELECT value FROM reading")
    row = cur.fetchone()
    if row is not None:
        print("float", row[0])

    # The description: the None-or-names shape and the column count. The
    # element itself differs (the bare name against CPython's 7-tuple), so
    # the probe prints the count and the presence only.
    cur = db.execute("SELECT id, name FROM job ORDER BY id")
    desc = cur.description
    print("desc is none", desc is None)
    if desc is not None:
        print("desc len", len(desc))
    cur = db.execute("INSERT INTO job (name) VALUES ('delta')")
    print("dml desc is none", cur.description is None)

    db.close()


main()
