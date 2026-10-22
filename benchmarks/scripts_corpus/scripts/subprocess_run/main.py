"""Run a command given on the command line and report its output."""

import subprocess
import sys


def main() -> int:
    if len(sys.argv) < 2:
        sys.stderr.write("usage: subprocess_run COMMAND [ARG ...]\n")
        return 2
    result = subprocess.run(sys.argv[1:], capture_output=True, text=True)
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
