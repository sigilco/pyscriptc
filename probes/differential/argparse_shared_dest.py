# Issue #1346: one dest shared across parser levels. A dest a parent parser
# and a subcommand (or two sibling subcommands) both register is one
# namespace attribute, as in CPython: the chosen level's default lands over
# the parent's, a token the chosen level consumes overwrites the default,
# and an unchosen sibling's default does not leak. The runs share one
# parser tree and parse several argv lists, and every run reads the shared
# dest only when the chosen subcommand registers it (an unchosen
# subcommand's attribute is a recorded divergence, not probed). The prog is
# set explicitly so the usage line does not depend on the script name.
import argparse


def show(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(prog="tool")
    parser.add_argument("--x", type=int, default=1)
    sub = parser.add_subparsers(dest="cmd", required=True)
    go = sub.add_parser("go", help="go mode")
    go.add_argument("--x", type=int, default=2)
    go.add_argument("word")
    stop = sub.add_parser("stop", help="stop mode")
    stop.add_argument("--x", type=int, default=3)
    args = parser.parse_args(argv)
    if args.cmd == "go":
        print("go", args.x, args.word)
    else:
        print("stop", args.x)


show(["go", "hi"])
show(["go", "--x", "9", "hi"])
show(["--x", "5", "go", "hi"])
show(["--x", "5", "stop"])
show(["stop"])
show(["go", "hi"])
