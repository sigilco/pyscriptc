def count_words(words: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for word in words:
        if word in counts:
            counts[word] = counts[word] + 1
        else:
            counts[word] = 1
    return counts


def top(counts: dict[str, int]) -> str:
    best: str = ""
    best_n: int = 0
    for word in sorted(counts.keys()):
        n: int = counts[word]
        if n > best_n:
            best = word
            best_n = n
    return best + " " + str(best_n)
