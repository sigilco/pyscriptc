"""List the members of a tar archive."""

import sys
import tarfile


def main() -> int:
    if len(sys.argv) != 2:
        sys.stderr.write("usage: tar_list ARCHIVE\n")
        return 2
    with tarfile.open(sys.argv[1], "r") as archive:
        for member in archive.getmembers():
            print(f"{member.size:>10} {member.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
