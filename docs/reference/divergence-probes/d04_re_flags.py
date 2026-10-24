import re

m: re.Match | None = re.search("a(b)c", "xxabcxx")
if m is not None:
    print(m.group(0))
parts: list[str] = re.split(r"[\d,]", "a1b,c")
print(",".join(parts))
print(re.sub(r"\d", "#", "a1b2"))
words: list[str] = re.findall("[a-z]+", "Ab9c")
print(",".join(words))
