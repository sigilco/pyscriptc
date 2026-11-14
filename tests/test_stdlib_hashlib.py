"""hashlib: the one-shot form and the streaming hash object.

The closed subset is md5, sha1, sha256, sha384 and sha512. The one-shot form
hashes a buffer in one call; the streaming form opens a hash object whose
update() folds bytes in and whose digest() and hexdigest() read the result
(issue #1248). Every row diffs against the CPython oracle byte for byte, and
the two paths must agree with each other on the same bytes.
"""

from __future__ import annotations

import hashlib as py_hashlib
from pathlib import Path

from tests.test_gates import compile_run, write_case


def _run(tmp_path: Path, name: str, source: str) -> str:
    path = write_case(tmp_path, name, source)
    got = compile_run(tmp_path, path, "run")
    assert got.returncode == 0, got.stderr
    return got.stdout


def test_the_streaming_object_matches_cpython(tmp_path: Path) -> None:
    source = "\n".join(
        [
            "import hashlib",
            "",
            "def run() -> int:",
            "    h = hashlib.sha256()",
            "    h.update(b'ab')",
            "    print(h.hexdigest())",
            "    m = hashlib.md5()",
            "    m.update(b'ab')",
            "    print(m.hexdigest())",
            "    s = hashlib.sha1()",
            "    s.update(b'ab')",
            "    print(s.hexdigest())",
            "    s3 = hashlib.sha384()",
            "    s3.update(b'ab')",
            "    print(s3.hexdigest())",
            "    s5 = hashlib.sha512()",
            "    s5.update(b'ab')",
            "    print(s5.hexdigest())",
            "    return 0",
            "",
        ]
    )
    expected = "\n".join(
        [
            py_hashlib.sha256(b"ab").hexdigest(),
            py_hashlib.md5(b"ab").hexdigest(),
            py_hashlib.sha1(b"ab").hexdigest(),
            py_hashlib.sha384(b"ab").hexdigest(),
            py_hashlib.sha512(b"ab").hexdigest(),
        ]
    )
    assert _run(tmp_path, "stream.py", source) == expected + "\n0\n"


def test_the_chunked_update_matches_the_one_shot(tmp_path: Path) -> None:
    source = "\n".join(
        [
            "import hashlib",
            "",
            "def run() -> int:",
            "    h = hashlib.sha256()",
            "    for _i in range(160):",
            "        h.update(b'K' * 65536)",
            "    print(h.hexdigest())",
            "    print(hashlib.sha256(b'K' * 10485760).hexdigest())",
            "    return 0",
            "",
        ]
    )
    digest = py_hashlib.sha256(b"K" * 10485760).hexdigest()
    assert _run(tmp_path, "chunked.py", source) == f"{digest}\n{digest}\n0\n"


def test_the_block_boundary_chunk_sizes_match_cpython(tmp_path: Path) -> None:
    source = "\n".join(
        [
            "import hashlib",
            "",
            "def run() -> int:",
            "    sizes: list[int] = [55, 56, 57, 63, 64, 65, 111, 112, 113, 127, 128, 129]",
            "    for s in sizes:",
            "        h = hashlib.sha256()",
            "        h.update(b'x' * s)",
            "        h.update(b'x' * s)",
            "        print(s, h.hexdigest())",
            "    return 0",
            "",
        ]
    )
    expected = "\n".join(
        f"{s} {py_hashlib.sha256(b'x' * s * 2).hexdigest()}" for s in [55, 56, 57, 63, 64, 65, 111, 112, 113, 127, 128, 129]
    )
    assert _run(tmp_path, "boundaries.py", source) == expected + "\n0\n"


def test_digest_returns_bytes_and_the_second_read_is_frozen(tmp_path: Path) -> None:
    source = "\n".join(
        [
            "import hashlib",
            "",
            "def run() -> int:",
            "    h = hashlib.sha256()",
            "    h.update(b'zz')",
            "    first: bytes = h.digest()",
            "    second: bytes = h.digest()",
            "    print(first == second)",
            "    print(first == hashlib.sha256(b'zz').digest())",
            "    empty = hashlib.sha256()",
            "    print(empty.hexdigest())",
            "    return 0",
            "",
        ]
    )
    expected = "\n".join(
        [
            "True",
            "True",
            py_hashlib.sha256(b"").hexdigest(),
        ]
    )
    assert _run(tmp_path, "digest.py", source) == expected + "\n0\n"


def test_the_one_shot_form_still_compiles_clean(tmp_path: Path) -> None:
    source = "\n".join(
        [
            "import hashlib",
            "",
            "def run() -> int:",
            "    print(hashlib.sha256(b'ab').hexdigest())",
            "    print(hashlib.sha256(b'ab').digest())",
            "    print(hashlib.md5(b'ab').hexdigest())",
            "    print(hashlib.sha1(b'ab').hexdigest())",
            "    print(hashlib.sha384(b'ab').hexdigest())",
            "    print(hashlib.sha512(b'ab').hexdigest())",
            "    return 0",
            "",
        ]
    )
    expected = "\n".join(
        [
            py_hashlib.sha256(b"ab").hexdigest(),
            str(py_hashlib.sha256(b"ab").digest()),
            py_hashlib.md5(b"ab").hexdigest(),
            py_hashlib.sha1(b"ab").hexdigest(),
            py_hashlib.sha384(b"ab").hexdigest(),
            py_hashlib.sha512(b"ab").hexdigest(),
        ]
    )
    assert _run(tmp_path, "oneshot.py", source) == expected + "\n0\n"


def test_the_from_import_form_compiles_clean(tmp_path: Path) -> None:
    source = "\n".join(
        [
            "from hashlib import sha256",
            "",
            "def run() -> int:",
            "    h = sha256()",
            "    h.update(b'ab')",
            "    print(h.hexdigest())",
            "    print(sha256(b'ab').hexdigest())",
            "    return 0",
            "",
        ]
    )
    digest = py_hashlib.sha256(b"ab").hexdigest()
    assert _run(tmp_path, "fromimport.py", source) == f"{digest}\n{digest}\n0\n"
