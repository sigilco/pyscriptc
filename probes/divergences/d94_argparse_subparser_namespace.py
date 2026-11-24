# SPDX-License-Identifier: Apache-2.0
# Divergence D94: the subparser namespace has no absent slot. The dest of an
# optional subcommand reads "" (CPython None), and an attribute of the
# subcommand that was not chosen reads its default (CPython raises
# AttributeError). The chosen subcommand's attributes match.
import argparse


def run(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(prog="tool")
    sub = parser.add_subparsers(dest="cmd")
    go = sub.add_parser("go")
    go.add_argument("--n", type=int, default=2)
    args = parser.parse_args(argv)
    print(repr(args.cmd))
    try:
        print(args.n)
    except AttributeError:
        print("AttributeError")


run(["go", "--n", "5"])
run([])
