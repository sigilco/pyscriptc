from __future__ import annotations
import hashlib
def main() -> None:
    h = hashlib.sha256()
    data: bytes = open("/tmp/x", "rb").read(64)
    h.update(data)
    print(h.hexdigest())
if __name__ == "__main__": main()
