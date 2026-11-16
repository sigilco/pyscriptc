# Issue #1268: the email builder surface. The probe runs on the pinned
# CPython oracle and on the compiled program: the stdout, stderr and exit
# code must match byte for byte.
#
# SPDX-License-Identifier: Apache-2.0
#
# The byte-comparable surface: `email.message.EmailMessage()` builds an
# empty message, `msg[name] = value` appends a header row (the unique-row
# refusal rides the run: a second Subject raises ValueError on both sides
# and the probe prints around it), `set_content` lands a text or a bytes
# body (7bit within the 78-column limit, quoted-printable past it, 8bit
# for a short non-ASCII text that as_string re-serializes as base64), and
# `as_string` folds the headers the way the default policy folds a plain
# str value (an RFC 2047 utf-8 run past the fold width).
from email.message import EmailMessage


def build_subject() -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = "Quarterly report"
    msg["X-Tag"] = "one"
    msg["X-Tag"] = "two"
    msg.set_content("plain body")
    return msg


def build_long() -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = "y" * 100
    msg.set_content("x" * 100)
    return msg


def build_atoms(value: str) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = value
    msg.set_content("b")
    return msg


def build_qp() -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = "wrap"
    msg.set_content("line1\n" + "z" * 90 + "\n")
    return msg


def build_bytes() -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = "attachment"
    msg.set_content(b"hello attachment", maintype="application", subtype="octet-stream")
    return msg


def run() -> int:
    # The flat build: headers in store order, then the body.
    print(build_subject().as_string(), end="=#=\n")

    # The fold: a long Subject becomes an RFC 2047 run.
    print(build_long().as_string(), end="=#=\n")

    # The fold's atoms: the specials stay in the atom, the fold lands
    # between them, the non-ASCII atom becomes an encoded word, and the
    # one-column-too-wide atom folds whole (the syntactic break).
    print(build_atoms("word " * 30).as_string(), end="=#=\n")
    print(build_atoms("a" * 40 + ";" + "b" * 40).as_string(), end="=#=\n")
    print(build_atoms("caf\u00e9 " * 20).as_string(), end="=#=\n")
    print(build_atoms("z" * 76).as_string(), end="=#=\n")
    print(build_atoms("z" * 77).as_string(), end="=#=\n")
    print(build_atoms("two words and caf\u00e9" * 10).as_string(), end="=#=\n")

    # The quoted-printable wrap: the long line takes the soft break.
    print(build_qp().as_string(), end="=#=\n")

    # The bytes body: base64 over application/octet-stream.
    print(build_bytes().as_string(), end="=#=\n")

    # The non-ASCII text: 8bit at build time, base64 through the generator.
    msg = EmailMessage()
    msg.set_content("caf\xe9 body")
    print(msg.as_string(), end="=#=\n")

    # The unique rows: a second Subject raises ValueError on both sides.
    msg = EmailMessage()
    msg["Subject"] = "first"
    try:
        msg["Subject"] = "second"
        print("second subject: allowed")
    except ValueError as exc:
        print("ValueError:", exc)

    # The cleared content: set_content drops the rows it owns, keeps the
    # rest, and re-lands its own three.
    msg = EmailMessage()
    msg["Subject"] = "kept"
    msg["Content-Type"] = "text/html"
    msg.set_content("body")
    print(msg.as_string(), end="=#=\n")
    return 0


run()
