"""Membership in a tuple of str constants, repeated."""


def bench(n: int) -> int:
    total: int = 0
    i: int = 0
    env_var: str = "python_implementation"
    while i < n:
        hit: int = 0
        if env_var in ("platform_python_implementation", "python_implementation"):
            hit = 1
        miss: int = 0
        if "nope" in ("platform_python_implementation", "python_implementation"):
            miss = 1
        total = total + hit + miss
        i = i + 1
    return total
