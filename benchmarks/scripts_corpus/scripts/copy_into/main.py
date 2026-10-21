"""Copy a file into a destination directory, creating the directory if needed."""

import os
import shutil
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 3:
        sys.stderr.write("usage: copy_into SRC DEST_DIR\n")
        return 2
    source: str = sys.argv[1]
    dest_dir: str = sys.argv[2]
    if not os.path.isfile(source):
        sys.stderr.write(f"copy_into: not a file: {source}\n")
        return 1
    if not os.path.isdir(dest_dir):
        os.makedirs(dest_dir)
    target: Path = Path(dest_dir).joinpath(Path(source).name)
    shutil.copyfile(source, target.as_posix())
    print(f"copied {Path(source).name} -> {target.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
