def longer(a: str, b: str) -> str:
    if len(a) >= len(b):
        return a
    return b


if __name__ == "__main__":
    print(longer("bb", "a"))
