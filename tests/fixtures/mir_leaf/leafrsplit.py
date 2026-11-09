def show(parts: list[str]) -> None:
    print(parts)


def edges(text: str) -> None:
    # `rsplit` scans right to left: maxsplit 0 keeps the whole string,
    # a maxsplit past the separator count splits everywhere, and
    # overlapping separators resolve from the right (`aaa` on `aa`).
    show(text.rsplit(",", 0))
    show(text.rsplit(",", 1))
    show(text.rsplit(",", 2))
    show(text.rsplit(",", 5))
    show(text.rsplit(","))


def main() -> None:
    edges("a,b,c,d")
    edges("abc")
    edges("")
    edges("a,")
    edges(",a")
    edges("a,,b")
    show("aaa".rsplit("aa", 1))
    show("aaa".rsplit("aa"))
    show("aaaa".rsplit("aa"))
    show("aaaaa".rsplit("aa", 2))
    show("ababab".rsplit("ab", 2))
    show("banana".rsplit("ana", 1))
    show("banana".rsplit("ana"))
    show("x,y,z".split(",", 1))
    show("x,y,z".split(",", 0))
    show("a b  c".split())
    show("a b  c".rsplit())


if __name__ == "__main__":
    main()
