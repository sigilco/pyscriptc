"""dict.keys() in insertion order. The checksum is n * 104, and a sorted order would differ."""


def bench(n: int) -> int:
    total: int = 0
    i: int = 0
    while i < n:
        nums: dict[int, int] = {2: i, 0: i, 1: i}
        acc: int = 0
        place: int = 1
        for key in nums.keys():
            acc = acc + key * place
            place = place * 10
        copied: list[int] = list(nums.keys())
        total = total + acc + copied[0]
        i = i + 1
    return total
