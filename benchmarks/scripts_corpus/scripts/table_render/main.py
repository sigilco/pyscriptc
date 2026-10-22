"""Render a text table with aligned columns."""

import sys


def widths(rows: list[list[str]]) -> list[int]:
    if not rows:
        return []
    sizes: list[int] = []
    idx: int = 0
    first: list[str] = rows[0]
    while idx < len(first):
        size: int = len(first[idx])
        for row in rows:
            if idx < len(row) and len(row[idx]) > size:
                size = len(row[idx])
        sizes.append(size)
        idx = idx + 1
    return sizes


def main() -> int:
    if len(sys.argv) != 2:
        sys.stderr.write("usage: table_render FILE\n")
        return 2
    rows: list[list[str]] = []
    with open(sys.argv[1]) as handle:
        for line in handle:
            text: str = line.rstrip("\n")
            if text == "":
                continue
            rows.append(text.split("|"))
    sizes: list[int] = widths(rows)
    for row in rows:
        parts: list[str] = []
        idx: int = 0
        for cell in row:
            parts.append(cell.ljust(sizes[idx] if idx < len(sizes) else len(cell)))
            idx = idx + 1
        print("| " + " | ".join(parts) + " |")
    print(f"{len(rows)} rows, {len(sizes)} columns")
    return 0


if __name__ == "__main__":
    sys.exit(main())
