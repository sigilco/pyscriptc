# /// script
# requires-python = ">=3.14"
# dependencies = [
#   "certifi",
# ]
# ///
"""The library receipt program (issue #409): certifi through the deps lane.

One PEP 723 script that is not vendored: the resolver pulls a real certifi
wheel from PyPI, the compiler lowers it from the unpacked wheel source, and
the wheel's data file (`cacert.pem`) is staged beside the emitted binary so
`importlib.resources` resolves there. Whatever this prints, CPython prints
from the same script: the runner and the differential test diff the two
outputs.

The prints are portable across the two lanes: the PEM byte length, its head
and tail slices, and the tail of `where()` after `certifi/` (`cacert.pem`)
with the full path shape as a boolean. The raw staged paths differ (the
unpacked cache root on CPython, `<binary>-data/` on the compiled side), so
the receipt asserts on the shape, never the host prefix.
"""

from certifi import contents, where


def main() -> None:
    pem: str = contents()
    path: str = where()
    data: bytes = pem.encode("utf-8")
    print(len(data))
    print(data[:5], data[-5:])
    print(path.rpartition("certifi/")[2])
    print(path.endswith("certifi/cacert.pem"))


if __name__ == "__main__":
    main()
