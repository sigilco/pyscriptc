# SPDX-License-Identifier: Apache-2.0
# Issue #1373: nargs= inside subcommands, REMAINDER after a subcommand, the
# add_subparsers/add_parser keywords (aliases, title, description, metavar,
# help, epilog), nested add_subparsers, and a parent that registers a
# positional before its subcommand holder. The runs share one parser tree and
# compare stdout; the error runs print the usage block and `tool: error:`.
import argparse
import sys


def build() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tool", description="job tool", epilog="see docs")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("target", nargs="?", default="here")
    sub = parser.add_subparsers(
        dest="cmd",
        required=True,
        title="commands",
        description="what to do",
        metavar="COMMAND",
        help="the action",
    )
    run = sub.add_parser("run", aliases=["r", "go"], help="run a program", description="run it", epilog="bye")
    run.add_argument("--env", nargs="+")
    run.add_argument("argv", nargs=argparse.REMAINDER)
    copy = sub.add_parser("copy", help="copy files")
    copy.add_argument("src")
    copy.add_argument("dest")
    copy.add_argument("--jobs", type=int, nargs="?", const=4, default=1)
    remote = sub.add_parser("remote", help="manage remotes")
    rsub = remote.add_subparsers(dest="action", required=True)
    add = rsub.add_parser("add", help="add a remote")
    add.add_argument("name")
    add.add_argument("urls", nargs="+")
    rm = rsub.add_parser("rm", aliases=["remove"])
    rm.add_argument("names", nargs="*", type=float)
    return parser


def show(argv: list[str]) -> None:
    args = build().parse_args(argv)
    if args.cmd in ("run", "r", "go"):
        print(args.cmd, args.verbose, args.target, args.argv)
    elif args.cmd == "copy":
        print(args.cmd, args.src, args.dest, args.jobs)
    elif args.action == "add":
        print(args.cmd, args.action, args.name, args.urls)
    else:
        print(args.cmd, args.action, args.names)


def parse(argv: list[str]) -> None:
    ns = build().parse_args(argv)
    print(ns.cmd)


empty: list[str] = []


def show_env(argv: list[str]) -> None:
    args = build().parse_args(argv)
    print(args.cmd, args.env, args.argv)


mode = sys.argv[1] if len(sys.argv) > 1 else "ok"
if mode == "ok":
    show(["run"])
    show(["run", "--env", "A=1", "B=2", "--", "ls", "-la"])
    show_env(["run", "--env", "A=1", "B=2", "--", "ls", "-la"])
    show(["run", "ls", "-la", "--env", "x"])
    show(["r", "make", "--verbose"])
    show(["--verbose", "there", "go", "x"])
    show(["copy", "a", "b"])
    show(["copy", "a", "b", "--jobs"])
    show(["copy", "--jobs", "8", "a", "b"])
    show(["remote", "add", "origin", "u1", "u2"])
    show(["remote", "rm"])
    show(["remote", "remove", "1.5", "2"])
elif mode == "help":
    parse(["-h"])
elif mode == "help_run":
    parse(["run", "-h"])
elif mode == "help_remote":
    parse(["remote", "-h"])
elif mode == "no_cmd":
    parse(empty)
elif mode == "bad_alias":
    parse(["nope"])
elif mode == "no_action":
    parse(["remote"])
elif mode == "no_urls":
    parse(["remote", "add", "origin"])
elif mode == "bad_float":
    parse(["remote", "rm", "x"])
elif mode == "no_env":
    parse(["run", "--env"])
elif mode == "few_args":
    parse(["copy", "a"])
elif mode == "extra":
    parse(["copy", "a", "b", "c"])
