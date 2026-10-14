"""String concatenation and len. This workload does not index or call methods."""


def repeat_len(n: int) -> int:
    piece: str = "ab"
    text: str = ""
    i: int = 0
    while i < n:
        text = text + piece
        i = i + 1
    return len(text)
