from __future__ import annotations


def main() -> None:
    f = open("/etc/hosts", "rb")
    data: bytes = f.read()
    print(len(data) > 0)


if __name__ == "__main__":
    main()
