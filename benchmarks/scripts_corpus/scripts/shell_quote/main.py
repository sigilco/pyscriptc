"""Quote arguments for a POSIX shell."""

import sys

SAFE: str = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-./=+"


def quote(text: str) -> str:
    if text == "":
        return "''"
    for char in text:
        if char not in SAFE:
            return "'" + text.replace("'", "'\\''") + "'"
    return text


def main() -> int:
    if len(sys.argv) < 2:
        sys.stderr.write("usage: shell_quote ARG [ARG ...]\n")
        return 2
    parts: list[str] = []
    for arg in sys.argv[1:]:
        parts.append(quote(arg))
    print(" ".join(parts))
    return 0


if __name__ == "__main__":
    sys.exit(main())
