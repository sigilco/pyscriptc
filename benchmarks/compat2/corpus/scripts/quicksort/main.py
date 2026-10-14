def quicksort(items: list[int], lo: int, hi: int) -> None:
    while lo < hi:
        pivot = items[(lo + hi) // 2]
        i = lo
        j = hi
        while i <= j:
            while items[i] < pivot:
                i += 1
            while items[j] > pivot:
                j -= 1
            if i <= j:
                items[i], items[j] = items[j], items[i]
                i += 1
                j -= 1
        if j - lo < hi - i:
            quicksort(items, lo, j)
            lo = i
        else:
            quicksort(items, i, hi)
            hi = j


seed = 12345
data: list[int] = []
for _ in range(5000):
    seed = (seed * 1103515245 + 12345) % 2147483648
    data.append(seed % 10000)
quicksort(data, 0, len(data) - 1)
print(data[0], data[2500], data[-1])
print(all(data[i] <= data[i + 1] for i in range(len(data) - 1)))
