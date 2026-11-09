def note(x: int) -> None:
    # A void function: the body carries no return value, so the MIR lane
    # lowers each `return` to a void `MIR_RET` and the fall-off-the-end
    # path to one more.
    if x > 0:
        return
    return


def counted(x: int) -> None:
    # A None-typed local is the singleton: it takes no slot and no alloca,
    # so the emitted frame holds only the scalar slots. Returning it is a
    # void return too: the value is the singleton, not a runtime value.
    marker: None = None
    total = 0
    while total < x:
        total = total + 1
    if x > 3:
        return marker
    return


def report(x: int) -> None:
    # A void return inside an `if` arm, and a void fall-off-the-end after
    # the arm: the merge block carries the trailing `ret void`.
    if x < 0:
        return
    nothing: None = None
    if x == 0:
        return nothing
    return


def main() -> None:
    # The module entry is in the slice when it is leaf-shaped: a void
    # function that falls off the end.
    pass


if __name__ == "__main__":
    main()
