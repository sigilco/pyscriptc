def steps(n: int) -> int:
    count = 0
    while n != 1:
        n = n // 2 if n % 2 == 0 else 3 * n + 1
        count += 1
    return count


best = 1
best_steps = 0
for i in range(1, 30000):
    s = steps(i)
    if s > best_steps:
        best = i
        best_steps = s
print(best, best_steps)
