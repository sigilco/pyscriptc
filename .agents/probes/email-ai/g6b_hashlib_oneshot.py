from __future__ import annotations

import hashlib


def main() -> None:
    data: bytes = open("/tmp/x", "rb").read(64)
    print(hashlib.sha256(data).hexdigest())


if __name__ == "__main__":
    main()
