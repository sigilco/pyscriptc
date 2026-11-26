# SPDX-License-Identifier: Apache-2.0
# Issue #415: `open(..., encoding=<str name>)` lowers to a runtime codec
# lookup, so a variable encoding (iniconfig's `encoding: str = "utf-8"`)
# opens the file the same way a literal does. The runtime accepts the
# spellings the lowered subset writes (utf-8, ascii, latin-1 and their
# aliases) and raises on an unknown one.
import os
import tempfile


def main() -> None:
    enc = "utf-8"
    path = os.path.join(tempfile.mkdtemp(), "note.txt")
    with open(path, "w", encoding=enc) as fp:
        fp.write("héllo")
    with open(path, encoding=enc) as fp:
        print(fp.read())
    os.unlink(path)


if __name__ == "__main__":
    main()
