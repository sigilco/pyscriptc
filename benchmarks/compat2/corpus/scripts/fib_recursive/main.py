def fib(n: int) -> int:
    if n < 2:
        return n
    return fib(n - 1) + fib(n - 2)


for n in (10, 20, 27):
    print(n, fib(n))
