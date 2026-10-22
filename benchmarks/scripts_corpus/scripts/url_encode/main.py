"""Percent-encode or decode a URL component."""

import sys
from urllib.parse import quote, unquote


def main() -> int:
    if len(sys.argv) != 3:
        sys.stderr.write("usage: url_encode encode|decode TEXT\n")
        return 2
    mode: str = sys.argv[1]
    payload: str = sys.argv[2]
    if mode == "encode":
        print(quote(payload, safe=""))
        return 0
    if mode == "decode":
        print(unquote(payload))
        return 0
    sys.stderr.write(f"url_encode: unknown mode: {mode}\n")
    return 2


if __name__ == "__main__":
    sys.exit(main())
