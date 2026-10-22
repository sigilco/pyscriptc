"""Print the SHA-256 digest of each file given on argv."""

import hashlib
import sys
from pathlib import Path


def digest(path: str) -> str:
    hasher = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            chunk: bytes = handle.read(65536)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def main() -> int:
    if len(sys.argv) < 2:
        sys.stderr.write("usage: checksum_files FILE [FILE ...]\n")
        return 2
    for path in sys.argv[1:]:
        print(f"{digest(path)}  {Path(path).name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
