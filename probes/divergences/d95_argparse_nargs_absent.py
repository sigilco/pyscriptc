# SPDX-License-Identifier: Apache-2.0
# Divergence D95: a list-valued nargs option (`*`, `+`, a count, REMAINDER) that
# does not appear reads an empty list (CPython None), because the namespace has
# no absent slot. A positional with nargs='*' reads [] on both sides.
import argparse

parser = argparse.ArgumentParser(prog="t")
parser.add_argument("--tags", nargs="+")
parser.add_argument("rest", nargs="*")
empty: list[str] = []
args = parser.parse_args(empty)
print(args.tags)
print(args.rest)
