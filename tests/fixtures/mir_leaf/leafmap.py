def tally(words: list[str]) -> int:
    counts: dict[str, int] = {}
    for word in words:
        counts[word] = counts.get(word, 0) + 1
    return len(counts)


def lookup(d: dict[int, int], k: int) -> int:
    if k in d:
        return d[k]
    return -1


def parts(text: str) -> int:
    ws = text.split()
    cs = text.split(",")
    rs = text.rsplit(",", 1)
    return len(ws) + len(cs) + len(rs)


def main() -> None:
    print(tally(["a", "b", "a"]))
    print(lookup({1: 10, 2: 20}, 2))
    print(parts("x y,z"))
    d: dict[str, str] = {"k": "v"}
    print(d["k"], len(d), "k" in d, "z" not in d)


if __name__ == "__main__":
    main()
