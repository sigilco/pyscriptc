"""Encode or decode base64 payloads given on argv."""

import base64
import sys


def main() -> int:
    if len(sys.argv) != 3:
        sys.stderr.write("usage: base64_codec encode|decode TEXT\n")
        return 2
    mode: str = sys.argv[1]
    payload: str = sys.argv[2]
    if mode == "encode":
        print(base64.b64encode(payload.encode("utf-8")).decode("ascii"))
        return 0
    if mode == "decode":
        print(base64.b64decode(payload.encode("ascii")).decode("utf-8"))
        return 0
    sys.stderr.write(f"base64_codec: unknown mode: {mode}\n")
    return 2


if __name__ == "__main__":
    sys.exit(main())
