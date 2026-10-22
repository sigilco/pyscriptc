"""Count the words of a text."""

SEPARATOR = " \t\n"


def count_words(text: str) -> dict[str, int]:
    """How often each word appears."""
    counts: dict[str, int] = {}
    for word in text.split():
        key: str = word.lower()
        if not key:
            continue
        counts[key] = counts.get(key, 0) + 1
    return counts


def top(counts: dict[str, int], limit: int) -> list[str]:
    """The most frequent words, most frequent first."""
    ranked: list[str] = sorted(counts.keys(), key=lambda word: -counts[word])
    return ranked[:limit]
