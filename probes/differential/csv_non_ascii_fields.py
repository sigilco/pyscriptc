# SPDX-License-Identifier: Apache-2.0
"""The CSV dialect over code points (issue #477).

`csv` works on `str` in CPython, so the field quoting rules apply to
code points and the text mode file I/O carries UTF-8. A field with a
non-ASCII code point and no delimiter or quote in it goes out as the
UTF-8 it is; a field with a comma, a quote or a newline in it is
quoted the excel dialect quotes it, and the doubled quote inside a
quoted field is one literal quote. The reader takes the bytes back
and hands the same text out. Both sides run this same program and the
lane byte-compares stdout, stderr and the exit code.
"""

import csv
import io


def main() -> None:
    buf: io.StringIO = io.StringIO()
    writer: csv.DictWriter = csv.DictWriter(buf, ["name", "note", "city", "emoji"])
    writer.writeheader()
    writer.writerow({"name": "café", "note": "x,y", "city": "line\nbreak", "emoji": "😀"})
    writer.writerow({"name": "naïve", "note": 'q"z', "city": "plain", "emoji": "中"})
    print(repr(buf.getvalue()))

    rows: list[dict[str, str | None]] = csv.DictReader(io.StringIO(buf.getvalue()))
    for row in rows:
        name: str | None = row["name"]
        note: str | None = row["note"]
        city: str | None = row["city"]
        emoji: str | None = row["emoji"]
        if name is None or note is None or city is None or emoji is None:
            print("missing")
        else:
            print(name)
            print(note)
            print(city)
            print(emoji)


main()
