"""Split a URL into its parts."""

import sys
from urllib.parse import urlsplit


def main() -> int:
    if len(sys.argv) < 2:
        sys.stderr.write("usage: url_split URL [URL ...]\n")
        return 2
    for arg in sys.argv[1:]:
        parts = urlsplit(arg)
        print(f"scheme {parts.scheme}")
        print(f"netloc {parts.netloc}")
        print(f"path {parts.path}")
        print(f"query {parts.query}")
        print(f"fragment {parts.fragment}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
