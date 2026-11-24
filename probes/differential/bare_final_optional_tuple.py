# SPDX-License-Identifier: Apache-2.0
"""A bare `Final` constant and `None` arms in an optional-field tuple return.

Issue #417 (pilot 2, iniconfig): `UTF8_BOM: Final = "..."` infers its type
from the value, and `return None, x` under `-> tuple[str | None, str | None]`
boxes each `None` into its field's optional. Both shapes are taken from
iniconfig 2.3.1's `_parse.py`.
"""

from typing import Final

COMMENTCHARS = "#;"
UTF8_BOM: Final = "\N{BYTE ORDER MARK}"


def parseline(line: str) -> tuple[str | None, str | None]:
    limit: Final = 1
    line = line.rstrip()
    if not line or line.lstrip()[:limit] in COMMENTCHARS:
        return None, None
    if line[0] == "[":
        if line[-1] == "]":
            return line[1:-1], None
        return None, line.strip()
    if not line[0].isspace():
        name, value = line.split("=", 1)
        return name.strip(), value.strip()
    return None, line.strip()


def show(part: str | None) -> str:
    if part is None:
        return "None"
    return repr(part)


def main() -> None:
    raw: str = UTF8_BOM + "[section]\nkey = value\n  more\n# note\n\n[bad\n"
    data: str = raw.removeprefix(UTF8_BOM)
    for line in data.splitlines():
        name, value = parseline(line)
        print(show(name), show(value))


if __name__ == "__main__":
    main()
