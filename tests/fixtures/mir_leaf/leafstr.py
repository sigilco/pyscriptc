def pick(n: int, s: str, t: str) -> str:
    out = "none"
    i = 0
    while i < n:
        if s == t:
            out = "same"
        else:
            out = "diff"
        i = i + 1
    return out
