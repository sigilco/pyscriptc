# SPDX-License-Identifier: Apache-2.0
# Issue #1373: argparse nargs= on add_argument. One parser exercises the
# positional and option forms in one run: `*` and `+` lists (typed), a count,
# `?` with a default, REMAINDER after a subcommand name, and an option that
# takes several values. The argv lists cover the empty tail, options between
# positionals, the `--` separator and a value that looks like an option after
# REMAINDER began.
import argparse


def build() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tool")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--pair", nargs=2, type=int)
    parser.add_argument("--tags", nargs="+")
    parser.add_argument("--level", nargs="?", const="high", default="low")
    parser.add_argument("name", nargs="?", default="anon")
    parser.add_argument("files", nargs="*")
    return parser


def show(argv: list[str]) -> None:
    args = build().parse_args(argv)
    print(args.verbose, args.name, args.files, args.level)


def show_pair(argv: list[str]) -> None:
    args = build().parse_args(argv)
    print(args.pair, args.tags)


show([])
show(["a"])
show(["a", "b", "c"])
show(["--verbose", "a", "b"])
show(["a", "--verbose", "b"])
show(["--level"])
show(["--level", "mid", "a"])
show(["--level=x", "a", "b"])
show(["--", "a", "--verbose"])
show_pair(["--pair", "3", "4", "--tags", "x", "y"])
show_pair(["--pair", "1", "2", "--tags", "z"])
