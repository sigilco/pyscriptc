# /// script
# requires-python = ">=3.14"
# dependencies = [
#   "iniconfig",
# ]
# ///
# SPDX-License-Identifier: Apache-2.0
"""The library receipt program (issue #415): iniconfig through the deps lane.

One PEP 723 script that is not vendored: the resolver pulls the iniconfig
wheel from PyPI, the compiler lowers it from the unpacked wheel source, and
the program parses an ini document with the package's documented API.
Whatever this prints, CPython prints from the same script: the runner and
the differential test diff the two outputs.

The prints are the package's own surface: section and key reads, a
multi-line continuation value, `lineof` (an `int | None`), membership,
and the `ParseError` text for a malformed document. The input is a
literal document passed as `data`, so neither lane reads a file and every
printed value is deterministic.
"""

import iniconfig

DOCUMENT = """\
# a pytest-style configuration
[pytest]
addopts = -ra -q
testpaths =
    tests
    integration
minversion: 8.0

[tool:coverage]
branch = true
fail_under = 90
"""

MALFORMED = "[pytest]\naddopts = -q\n  continued\n[]\n"


def main() -> None:
    ini = iniconfig.IniConfig("receipt.ini", data=DOCUMENT)
    pytest_section = ini["pytest"]
    print(pytest_section["addopts"])
    print(pytest_section["testpaths"].split("\n"))
    print(pytest_section["minversion"])
    print("pytest" in ini, "missing" in ini)
    print(ini.lineof("pytest"), ini.lineof("pytest", "minversion"), ini.lineof("missing"))
    coverage = ini["tool:coverage"]
    print(int(coverage["fail_under"]) + 1, coverage["branch"] == "true")
    try:
        iniconfig.IniConfig("broken.ini", data=MALFORMED)
    except iniconfig.ParseError as err:
        print(err)
        print(err.lineno, err.msg)


if __name__ == "__main__":
    main()
