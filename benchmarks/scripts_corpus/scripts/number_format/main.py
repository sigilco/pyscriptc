"""Format numbers with fixed, percent and thousands separators."""

import sys


def thousands(value: int) -> str:
    text: str = str(value)
    out: str = ""
    count: int = 0
    idx: int = len(text) - 1
    while idx >= 0:
        out = text[idx] + out
        count = count + 1
        if count % 3 == 0 and idx > 0:
            out = "," + out
        idx = idx - 1
    return out


def main() -> int:
    if len(sys.argv) != 2:
        sys.stderr.write("usage: number_format FILE\n")
        return 2
    with open(sys.argv[1]) as handle:
        for line in handle:
            text: str = line.strip()
            if text == "":
                continue
            value: float = float(text)
            print(f"{value:.2f} {value * 100:.1f}% {thousands(int(value))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
