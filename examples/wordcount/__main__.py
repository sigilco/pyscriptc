import tally

text = "one two two three three three"
words: list[str] = text.split()
counts: dict[str, int] = tally.count_words(words)
print(tally.top(counts))
