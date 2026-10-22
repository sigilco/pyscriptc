"""Flatten a nested JSON document into dotted key paths."""

import json
import sys
from pathlib import Path


def flatten(value: dict[str, list[str]], prefix: str, out: dict[str, str]) -> None:
    for key in sorted(value.keys()):
        child: str = key if not prefix else prefix + "." + key
        item: list[str] = value[key]
        if len(item) == 1:
            out[child] = item[0]
        elif len(item) > 1:
            head: str = item[0]
            rest: list[str] = item[1:]
            out[child] = head + "," + ",".join(rest)
        else:
            out[child] = ""


def main() -> int:
    if len(sys.argv) != 2:
        sys.stderr.write("usage: json_flatten FILE\n")
        return 2
    text: str = Path(sys.argv[1]).read_text()
    document: dict[str, list[str]] = json.loads(text)
    out: dict[str, str] = {}
    flatten(document, "", out)
    for key in sorted(out.keys()):
        print(f"{key} = {out[key]}")
    print(f"{len(out)} leaves")
    return 0


if __name__ == "__main__":
    sys.exit(main())
