# Issue #1228: the closed email read surface. The probe runs on the pinned
# CPython oracle and on the compiled program: the stdout, stderr and exit
# code must match byte for byte.
#
# The byte-comparable surface: a flat message's headers (case-insensitive
# get, the None miss, keys in wire order, get_all over a repeated header,
# the decoded items pairs), the default content type of a message without
# a Content-Type row, and a MIME message with an attachment: the multipart
# walk in pre-order, the attachment's decoded filename, its base64 payload
# and the quoted-printable decode of a text part.
#
# The seams that are recorded in divergence 73 (D73) instead of probed
# here: the folded header value keeps the fold's newline where CPython's
# raw form does too, an encoded word in an unlisted charset keeps the raw
# word, and as_string() re-emits the parsed record, not the wire form.
import email

FLAT = (
    "From: sender@example.com\n"
    "To: rcpt@example.com\n"
    "Subject: Quarterly report\n"
    "X-Tag: one\n"
    "X-Tag: two\n"
    "Message-ID: <q3@example.com>\n"
    "\n"
    "plain body\n"
)

MIME = (
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


def run() -> int:
    flat = email.message_from_string(FLAT)
    subject: str | None = flat.get("Subject")
    print(subject is not None and subject == "Quarterly report")
    print(flat.get("subject") == flat.get("SUBJECT"))
    print(flat.get("No-Such-Header") is None)
    names: list[str] = flat.keys()
    print(len(names), names[0], names[2])
    tags: list[str] | None = flat.get_all("X-Tag")
    print(tags == ["one", "two"])
    missing_all: list[str] | None = flat.get_all("Missing")
    print(missing_all is None)
    print(flat.get_content_type())
    body = flat.get_payload()
    print(body == "plain body\n")

    tags2: list[str] | None = flat.get_all("x-tag")
    if tags2 is not None:
        print(len(tags2), tags2[0])

    mime = email.message_from_string(MIME)
    print(mime.get_content_type(), mime.is_multipart())
    kinds: list[str] = []
    for part in mime.walk():
        kinds.append(part.get_content_type())
    print(kinds)
    for part in mime.walk():
        name: str | None = part.get_filename()
        if name is not None:
            print(name)
            data = part.get_payload(decode=True)
            print(data == b"hello attachment")
        elif part.get_content_type() == "text/plain":
            text = part.get_payload(decode=True)
            ok = isinstance(text, bytes)
            if ok:
                ok = text == "café body line\n".encode()
            print(ok)
    return 0


run()
