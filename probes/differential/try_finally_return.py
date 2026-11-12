def f(x: int) -> int:
    try:
        if x > 0:
            return x
        return -x
    finally:
        print("cleanup", x)


print(f(3), f(-2))
