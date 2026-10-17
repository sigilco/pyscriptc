---
title: Examples
description: Small programs that compile and run today.
---

## Word frequency

```python
import sys

with open(sys.argv[1]) as f:
    text = f.read()

counts = {}
for word in text.split():
    counts[word] = counts.get(word, 0) + 1

for word in sorted(counts, key=lambda w: counts[w]):
    print(word, counts[word])
```

```bash
pyscriptc compile wordfreq.py -o wordfreq
./wordfreq input.txt
```

## Fibonacci

```python
def fib(n: int) -> int:
    a, b = 0, 1
    for _ in range(n):
        a, b = b, a + b
    return a

print(fib(30))
```

## Exit codes and argv

```python
import sys

if len(sys.argv) < 2:
    print("usage: tool FILE")
    sys.exit(2)

with open(sys.argv[1]) as f:
    print(len(f.read().split()))
```

More corpus programs live in [`benchmarks/compat2/corpus/`](https://github.com/sigilco/pyscriptc/tree/main/benchmarks/compat2) in the repo.
