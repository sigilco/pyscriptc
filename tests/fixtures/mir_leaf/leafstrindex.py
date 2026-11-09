def at(s: str, i: int) -> str:
    # One-character read through `ps_str_get`.
    return s[i]


def codes(s: str) -> int:
    # `ord` of each character summed: the str arg splits into buffer
    # parts for the `ps_ord` call.
    total = 0
    i = 0
    while i < len(s):
        total = total + ord(s[i])
        i = i + 1
    return total


def shift_char(c: str, k: int) -> str:
    # `chr` of an int arithmetic result: the `ps_chr` buffer-out call.
    return chr((ord(c) - 97 + k) % 26 + 97)


def main() -> None:
    print(at("hello", 1))
    print(codes("abc"))
    print(shift_char("a", 3))


if __name__ == "__main__":
    main()
