from __future__ import annotations
def main() -> None:
    f = open("/etc/hosts", "rb")
    data: bytes = f.read(100)
    print(len(data))
if __name__ == "__main__": main()
