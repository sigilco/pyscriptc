def label(n: int) -> str:
    if n > 0:
        return "pos"
    return "neg"


def go(n: int) -> str:
    return label(n)
