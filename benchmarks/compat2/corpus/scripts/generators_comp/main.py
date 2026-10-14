def squares(n: int):
    for i in range(n):
        yield i * i


print(sum(squares(100)))
print({i: i % 3 for i in range(8)})
print({x % 5 for x in range(40)} == {0, 1, 2, 3, 4})
print([(a, b) for a in range(3) for b in range(a)])
print(list(zip("abc", range(3))), list(enumerate("xy", 1)))
