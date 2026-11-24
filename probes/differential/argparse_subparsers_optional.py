# Issue #1346: a parser whose subcommand is optional (add_subparsers without
# required=True). Running with no subcommand is legal and the parent options
# keep their values; the subcommand dest is not read here, because an absent
# subcommand reads "" in the native namespace and None in CPython (a recorded
# divergence). The probe ends on a subcommand error path: a bad int value
# inside `run` prints the subcommand usage and the `tool run: error:` prefix
# on stderr and exits 2, byte for byte.
import argparse


def parse(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(prog="tool")
    parser.add_argument("--level", type=int, default=1)
    sub = parser.add_subparsers(dest="action")
    run = sub.add_parser("run", help="run it")
    run.add_argument("--count", type=int, default=2)
    run.add_argument("--target", default="all")
    args = parser.parse_args(argv)
    if args.action == "run":
        print("run", args.level, args.count, args.target)
    else:
        print("none", args.level)


parse([])
parse(["--level", "4"])
parse(["run"])
parse(["--level", "2", "run", "--count", "9", "--target", "x"])
parse(["run", "--count", "x"])
