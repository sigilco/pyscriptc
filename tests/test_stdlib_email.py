"""email: the closed MIME read surface.

The parsed headers, the multipart walk, the decoded payloads and the
attachment names diff against the CPython oracle byte for byte: the header
lookup is case-insensitive, a missing header answers None, `keys` and
`items` report the wire order, and the quoted-printable and base64
transfer encodings decode the way CPython's do.

The fences are the ones the closed subset draws: a name outside the
surface refuses with PS2001, a computed `decode` flag refuses with
PS1003, and a keyword the subset does not read refuses with PS2003.
"""

from __future__ import annotations

import email as py_email
from pathlib import Path

from tests.test_gates import compile_run, write_case

from pyscriptc.lower import lower_source

_FLAT = (
    "From: sender@example.com\n"
    "To: rcpt@example.com\n"
    "Subject: Quarterly report\n"
    "X-Tag: one\n"
    "X-Tag: two\n"
    "Message-ID: <q3@example.com>\n"
    "\n"
    "plain body\n"
)

_MIME = (
    "From: sender@example.com\n"
    "Subject: Attachment report\n"
    "MIME-Version: 1.0\n"
    'Content-Type: multipart/mixed; boundary="BOUND"\n'
    "\n"
    "--BOUND\n"
    "Content-Type: text/plain\n"
    "Content-Transfer-Encoding: quoted-printable\n"
    "\n"
    "caf=C3=A9 body=\n"
    " line\n"
    "--BOUND\n"
    'Content-Type: application/octet-stream; name="data.bin"\n'
    'Content-Disposition: attachment; filename="data.bin"\n'
    "Content-Transfer-Encoding: base64\n"
    "\n"
    "aGVsbG8gYXR0YWNobWVudA==\n"
    "--BOUND--\n"
    "epilogue text\n"
)


def _oracle_bytes(tmp_path: Path, name: str, source: str) -> str:
    """The compiled output must equal the CPython oracle's output."""
    path = write_case(tmp_path, name, source)
    got = compile_run(tmp_path, path, "run")
    assert got.returncode == 0, got.stderr
    return got.stdout


def _fence(source: str, code: str, message: str) -> None:
    mod = lower_source(source)
    fences = mod["fences"]
    assert fences, source
    assert fences[0]["code"] == code, fences
    assert message in fences[0]["message"], fences


def test_headers_match_cpython(tmp_path: Path) -> None:
    source = "\n".join(
        [
            "import email",
            "",
            "def run() -> int:",
            "    msg = email.message_from_string(RAW)",
            "    subject: str | None = msg.get('Subject')",
            "    print(subject is not None)",
            "    if subject is not None:",
            "        print(subject)",
            "    print(msg.get('subject') == msg.get('SUBJECT'))",
            "    print(msg.get('No-Such-Header') is None)",
            "    names: list[str] = msg.keys()",
            "    print(len(names))",
            "    print(names[0])",
            "    tags: list[str] | None = msg.get_all('X-Tag')",
            "    print(tags == ['one', 'two'])",
            "    missing: list[str] | None = msg.get_all('Missing')",
            "    print(missing is None)",
            "    print(msg.get_content_type())",
            "    return 0",
            "",
            "RAW = " + repr(_FLAT),
            "",
        ]
    )
    expected = "\n".join(
        [
            "True",
            "Quarterly report",
            "True",
            "True",
            "6",
            "From",
            "True",
            "True",
            "text/plain",
            "0",
        ]
    )
    assert _oracle_bytes(tmp_path, "headers.py", source) == expected + "\n"


def test_items_match_cpython(tmp_path: Path) -> None:
    source = "\n".join(
        [
            "import email",
            "",
            "def run() -> int:",
            "    msg = email.message_from_string(RAW)",
            "    for name, value in msg.items():",
            "        print(name, value)",
            "    return 0",
            "",
            "RAW = " + repr(_FLAT),
            "",
        ]
    )
    msg = py_email.message_from_string(_FLAT)
    expected = "\n".join(f"{name} {value}" for name, value in msg.items())
    assert _oracle_bytes(tmp_path, "items.py", source) == expected + "\n0\n"


def test_multipart_walk_matches_cpython(tmp_path: Path) -> None:
    source = "\n".join(
        [
            "import email",
            "",
            "def run() -> int:",
            "    msg = email.message_from_string(RAW)",
            "    print(msg.get_content_type())",
            "    print(msg.is_multipart())",
            "    for part in msg.walk():",
            "        print(part.get_content_type(), part.is_multipart())",
            "    return 0",
            "",
            "RAW = " + repr(_MIME),
            "",
        ]
    )
    msg = py_email.message_from_string(_MIME)
    expected = "\n".join(
        [msg.get_content_type(), str(msg.is_multipart())]
        + [f"{part.get_content_type()} {part.is_multipart()}" for part in msg.walk()]
    )
    assert _oracle_bytes(tmp_path, "walk.py", source) == expected + "\n0\n"


def test_payload_decodes_match_cpython(tmp_path: Path) -> None:
    source = "\n".join(
        [
            "import email",
            "",
            "def run() -> int:",
            "    msg = email.message_from_string(RAW)",
            "    for part in msg.walk():",
            "        name: str | None = part.get_filename()",
            "        if name is not None:",
            "            print(name)",
            "            data = part.get_payload(decode=True)",
            "            print(data == b'hello attachment')",
            "        elif part.get_content_type() == 'text/plain':",
            "            text = part.get_payload(decode=True)",
            "            print(text == b'caf\\xc3\\xa9 body line')",
            "    return 0",
            "",
            "RAW = " + repr(_MIME),
            "",
        ]
    )
    expected = "\n".join(
        [
            "False",
            "data.bin",
            "True",
        ]
    )
    assert _oracle_bytes(tmp_path, "payload.py", source) == expected + "\n0\n"


def test_bytes_input_matches_cpython(tmp_path: Path) -> None:
    source = "\n".join(
        [
            "import email",
            "",
            "def run() -> int:",
            "    msg = email.message_from_bytes(b'Subject: Hi\\nX-A: 1\\n\\nbody\\n')",
            "    subject: str | None = msg.get('Subject')",
            "    if subject is not None:",
            "        print(subject)",
            "    print(len(msg.keys()))",
            "    return 0",
            "",
        ]
    )
    assert _oracle_bytes(tmp_path, "bytes.py", source) == "Hi\n2\n0\n"


def test_out_of_subset_names_fence() -> None:
    _fence(
        "import email\n"
        "def run() -> int:\n"
        "    msg = email.message_from_string('Subject: Hi\\n\\nbody\\n')\n"
        "    print(msg.set_payload('x'))\n"
        "    return 0\n",
        "PS2001",
        "not in the lowered subset",
    )


def test_unlisted_module_function_fences() -> None:
    _fence(
        "import email\ndef run() -> int:\n    print(email.mime_text('hi'))\n    return 0\n",
        "PS2001",
        "not in the lowered subset",
    )


def test_computed_decode_flag_fences() -> None:
    _fence(
        "import email\n"
        "def run() -> int:\n"
        "    flag = True\n"
        "    msg = email.message_from_string('Subject: Hi\\n\\nbody\\n')\n"
        "    print(msg.get_payload(decode=flag))\n"
        "    return 0\n",
        "PS1003",
        "decode is a constant bool",
    )


def test_unread_keyword_fences() -> None:
    _fence(
        "import email\n"
        "def run() -> int:\n"
        "    msg = email.message_from_string('Subject: Hi\\n\\nbody\\n')\n"
        "    print(msg.get('Subject', failobj='x'))\n"
        "    return 0\n",
        "PS2003",
        "keyword arguments are not lowered",
    )


def test_multipart_payload_raises(tmp_path: Path) -> None:
    """A multipart part's payload is the list of children in CPython, which
    is outside the closed surface: the call raises at run time."""
    source = "\n".join(
        [
            "import email",
            "",
            "def run() -> int:",
            "    msg = email.message_from_string(RAW)",
            "    print(msg.get_payload())",
            "    return 0",
            "",
            "RAW = " + repr(_MIME),
            "",
        ]
    )
    path = write_case(tmp_path, "multipart_payload.py", source)
    got = compile_run(tmp_path, path, "run")
    assert got.returncode != 0
    assert "get_payload on a multipart message" in got.stderr, got.stderr
