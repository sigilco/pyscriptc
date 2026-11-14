from __future__ import annotations

import socket


def main() -> None:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 8085))
    s.listen(1)
    conn, addr = s.accept()
    req: bytes = conn.recv(4096)
    print(len(req) > 0)


if __name__ == "__main__":
    main()
