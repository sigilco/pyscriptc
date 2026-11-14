from __future__ import annotations

import tarfile


def main() -> None:
    with tarfile.open("/tmp/x.tar.gz", "r:gz") as t:
        names: list[str] = t.getnames()
    print(names)


if __name__ == "__main__":
    main()
