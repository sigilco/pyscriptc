"""tuple[int, ...] is the list struct.unpack already returns.

The checksum is n steps of length, one index, and the sum. The value is a list,
so the annotation and the list literal disagree for mypy.
"""


def bench(n: int) -> int:
    total: int = 0
    i: int = 0
    while i < n:
        nums: tuple[int, ...] = [i, i + 5, 9]  # type: ignore[assignment]
        total = total + len(nums) + nums[1] + sum(nums)
        i = i + 1
    return total
