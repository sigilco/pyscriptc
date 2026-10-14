def tidy(text: str) -> str:
    return text.strip().lower()


def starts(text: str) -> int:
    if text.startswith("ab"):
        return 1
    return 0
