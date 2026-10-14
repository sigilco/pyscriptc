text = "the quick brown fox jumps over the lazy dog the fox barks and the dog runs away from the quick fox"
counts: dict[str, int] = {}
for word in text.split():
    counts[word] = counts.get(word, 0) + 1
for word in sorted(counts, key=lambda w: (-counts[w], w)):
    print(word, counts[word])
