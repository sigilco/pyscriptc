import sys


def p_str() -> None:
    # A str argument has no MIR tag (the lane carries w/b/d only), so
    # the function stays out of the slice.
    print("hi")


def p_bytes() -> None:
    print(b"x")


def p_sep() -> None:
    # `sep=`/`end=` ride the call record; the slice emits the defaults.
    print(1, sep=",")


def p_end() -> None:
    print(1, end="")


def p_stderr() -> None:
    # `file=sys.stderr` rides the stream tag; the slice prints stdout.
    print(1, file=sys.stderr)


def p_stdout() -> None:
    # `file=sys.stdout` is the default stream: identical emission.
    print(2, file=sys.stdout)


def main() -> None:
    pass
