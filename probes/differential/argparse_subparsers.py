# Issue #1346: argparse subparsers (add_subparsers(dest=, required=) and
# add_parser(name, help=)) over the lowered option set. The runs share one
# parser tree and parse several argv lists: a subcommand with typed options
# and defaults, a subcommand with a positional, an option on the parent
# before the subcommand, and a parent without a required subcommand. The
# probe ends on an error path: the missing required subcommand prints the
# parent usage and `tool: error: ...` on stderr and exits 2, so the lane
# compares stdout, stderr and the exit code byte for byte. The prog is set
# explicitly so the usage line does not depend on the script name. The
# namespace only reads the attributes the chosen subcommand registers: an
# unchosen subcommand's attribute is a recorded divergence, not probed.
import argparse


def build() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tool", description="queue tool")
    parser.add_argument("--verbose", action="store_true")
    sub = parser.add_subparsers(dest="cmd", required=True)
    enqueue = sub.add_parser("enqueue", help="queue a job")
    enqueue.add_argument("--name", "-n", required=True)
    enqueue.add_argument("--retries", type=int, default=3)
    enqueue.add_argument("--force", action="store_true")
    status = sub.add_parser("status", help="show a job")
    status.add_argument("job")
    status.add_argument("--ratio", type=float, default=0.5)
    return parser


def show(argv: list[str]) -> None:
    parser = build()
    args = parser.parse_args(argv)
    if args.cmd == "enqueue":
        print(args.cmd, args.verbose, args.name, args.retries, args.force)
    else:
        print(args.cmd, args.verbose, args.job, args.ratio)


show(["enqueue", "--name", "a"])
show(["enqueue", "-n", "b", "--retries", "7", "--force"])
show(["--verbose", "enqueue", "--name=c"])
show(["status", "job-1"])
show(["status", "job-2", "--ratio", "2.5"])
show([])
