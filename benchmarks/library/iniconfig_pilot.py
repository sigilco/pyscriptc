# /// script
# requires-python = ">=3.14"
# dependencies = [
#   "iniconfig",
# ]
# ///
# SPDX-License-Identifier: Apache-2.0
"""Pilot 2 (issue #417): iniconfig through the deps lane, a real parser on a real input.

One PEP 723 script that is not vendored: the resolver pulls the iniconfig
wheel from PyPI, the compiler lowers it from the unpacked wheel source, and
the program parses an ini document with the package's documented API. The
runner (`benchmarks/library/pilot2.py`) and the differential test diff the
compiled binary's output against CPython running this same script.

The input is a literal document passed as `data`, so neither lane reads a
file and every printed value is deterministic: section and key reads, a
multi-line continuation value, `lineof`, membership, and the `ParseError`
text for a malformed document. `IniConfig.get` is left out on purpose: its
`_D | _T | str | None` return is an optional of a multi-arm union (PS1102),
a gap of its own that the pilot does not need to prove the library path.
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
    ini = iniconfig.IniConfig("pilot.ini", data=DOCUMENT)
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
