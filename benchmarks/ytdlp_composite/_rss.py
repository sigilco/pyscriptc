#!/usr/bin/env python3
"""Run a command and print its peak RSS in KiB on stdout (GNU time -v style).

Used by the yt-dlp composite runner to measure the peak resident set size of
one candidate process without depending on GNU time being installed.

    python _rss.py -- <argv...>
"""

import os
import resource
import subprocess
import sys


def main() -> int:
    argv = sys.argv[1:]
    if argv and argv[0] == "--":
        argv = argv[1:]
    if not argv:
        print("usage: _rss.py -- <argv...>", file=sys.stderr)
        return 2
    proc = subprocess.Popen(
        argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=os.environ
    )
    proc.wait()
    usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    sys.stdout.write(str(usage.ru_maxrss))
    return 0


if __name__ == "__main__":
    sys.exit(main())
