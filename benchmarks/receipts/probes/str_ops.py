# SPDX-License-Identifier: Apache-2.0
"""String methods, slicing and formatting.

The string surface: the common methods, the slices, the joins and the
f-strings. The output is ASCII, so the comparison never depends on the
locale or on the encoding of the host.
"""

s: str = "Hello, World"

print(s.upper(), s.lower())
print(s.split(", "))
print(s.replace("World", "pyscriptc"))
print(s.find("World"), s.find("World", 5))
print(s.startswith("Hello"), s.endswith("World"))
print(s[0], s[-1], s[0:5], s[7:])
print(len(s))
print("-".join(["a", "b", "c"]))
print("  padded  ".strip())
print(s.count("l"))
print("a,b;c".split(","))
print(f"{s} has {len(s)} chars")
print(s[::2])
print("x" * 3)
