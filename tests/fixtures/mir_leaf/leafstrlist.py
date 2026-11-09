def build() -> str:
    out = []
    out.append("a")
    out.append("b")
    return "-".join(out)


def pick(items: list[str], i: int) -> str:
    return items[i]


def main() -> None:
    print(build())
    print(pick(["x", "y"], 1))
    print(len(["p", "q"]))
    print(",".join(["m", "n"]))


if __name__ == "__main__":
    main()
