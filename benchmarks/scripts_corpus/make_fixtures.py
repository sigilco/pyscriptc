"""Build the corpus fixtures, so a fresh checkout can reproduce the oracle.

The fixtures are committed, but they carry a modification time that a checkout
does not preserve. `old_files` reads that time, so the generator sets it to a
fixed value. Run it after changing any fixture:

    uv run python -m benchmarks.scripts_corpus.make_fixtures
"""

from __future__ import annotations

import os
import tarfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures"

# The fixed modification time the generator stamps on the tree fixture.
TREE_MTIME: int = 1_000_000_000

TREE_FILES: dict[str, str] = {
    "tree/a.txt": "x\ny\n",
    "tree/sub/b.txt": "zz\n",
    "tree/Empty Name.TXT": "one\n",
    "tree/sub/with space.txt": "two\n",
}

PLAIN_FILES: dict[str, str] = {
    "lines.txt": "alpha\nbeta\ngamma\nbeta\nalpha\ndelta\n",
    "lines2.txt": "alpha\ndelta\nepsilon\n",
    "columns.csv": "name,value,count\nalice,10.5,2\nbob,20.25,3\ncarol,30.125,4\nalice,5.5,1\n",
    "numbers.csv": "a,b,c\n1.5,2,3\n4.25,5,6\n7,8.5,9\n",
    "people.csv": "name,team\nalice,red\ndave,blue\n",
    "records.json": (
        '[{"name": "alice", "role": "admin"}, {"name": "bob", "role": "user"}, {"name": "carol", "team": "red"}]'
    ),
    "flat.json": '{"a": ["1", "2"], "b": ["3"], "c": ["4", "5", "6"]}',
    "a.env": "host=alpha\nport=8080\nshared=one\n",
    "b.env": "port=9090\nshared=two\nextra=yes\n",
    "access.log": (
        '10.0.0.1 - - [21/Oct/2026:12:00:00 +0000] "GET /api/users?id=1 HTTP/1.1" 200 512\n'
        '10.0.0.2 - - [21/Oct/2026:12:00:01 +0000] "POST /api/users HTTP/1.1" 201 128\n'
        '10.0.0.1 - - [21/Oct/2026:12:00:02 +0000] "GET /api/users?id=2 HTTP/1.1" 200 256\n'
        '10.0.0.3 - - [21/Oct/2026:12:00:03 +0000] "GET /health HTTP/1.1" 200 64\n'
        '10.0.0.2 - - [21/Oct/2026:12:00:04 +0000] "GET /missing HTTP/1.1" 404 32\n'
        "garbage line\n"
    ),
    "app.log": (
        "worker starting up\nworker job 1\nworker job 2\nweb starting up\nweb request 1\nweb request 2\nweb request 3\n"
    ),
    "dates.txt": (
        "meeting on 2026-10-12 confirmed\n"
        "release 2026-11-01 planned\n"
        "review 2026-10-05 done\n"
        "no date here\n"
        "retro 2026-10-26 soon\n"
    ),
    "sample.ini": "; global comment\n[server]\nhost = localhost\nport = 8000\n\n[client]\ntimeout = 30\nretries = 3\n",
    "sample.md": "# Title\n\nSome **bold** and `code` text.\nA __strong__ line follows.\n",
    "numbers.txt": "10\n-3\n7.25\n1000\n0.5\n",
    "table.txt": "id|name|size\n1|alpha|10\n2|beta|20\n30|gamma|300\n",
    "template.txt": "Hello {NAME}, welcome to version {VERSION}.\n",
    "bytes.bin": "a\nb\nc",
}


def stamp_mtimes() -> None:
    """Stamp the tree fixture modification times a checkout cannot preserve.

    `old_files` reads the modification time of the tree fixture and prints
    it, so a fresh checkout (whose mtimes are the checkout time) would
    report no old files at all. This restores the fixed value without
    rewriting the fixture content.
    """

    for relative in TREE_FILES:
        path: Path = FIXTURES / relative
        if path.is_file():
            os.utime(path, (TREE_MTIME, TREE_MTIME))
    keep: Path = FIXTURES / "tree" / "empty" / ".keep"
    if keep.is_file():
        os.utime(keep, (TREE_MTIME, TREE_MTIME))


def write_tree() -> None:
    for relative, text in TREE_FILES.items():
        path: Path = FIXTURES / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        os.utime(path, (TREE_MTIME, TREE_MTIME))
    keep: Path = FIXTURES / "tree" / "empty" / ".keep"
    keep.parent.mkdir(parents=True, exist_ok=True)
    keep.write_text("", encoding="utf-8")
    os.utime(keep, (TREE_MTIME, TREE_MTIME))


def write_tar() -> None:
    """A small tar archive with two members, for the tar_list script."""

    archive_path: Path = FIXTURES / "bundle.tar"
    names: list[str] = ["tree/a.txt", "tree/sub/b.txt"]
    with tarfile.open(archive_path, "w") as archive:
        for name in names:
            source: Path = FIXTURES / name
            archive.add(source, arcname=name)


def main() -> int:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    for relative, text in PLAIN_FILES.items():
        path: Path = FIXTURES / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    write_tree()
    write_tar()
    stamp_mtimes()
    print(f"fixtures written to {FIXTURES}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
