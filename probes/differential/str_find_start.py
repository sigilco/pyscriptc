# SPDX-License-Identifier: Apache-2.0
"""`str.find` with a start index (issue #1246).

`find(needle, start)` searches from `start` on. A negative `start` counts
from the end of the string like a subscript does, a `start` past the end
finds nothing, and an empty needle at the end of the string is found there
while an empty needle past the end is not. Both sides run this same
program and the lane byte-compares stdout.
"""

s: str = "hello world"

print(s.find("world"))
print(s.find("world", 3))
print(s.find("world", 6))
print(s.find("world", 7))
print(s.find("o", 5))
print(s.find("", 11))
print(s.find("", 12))
print(s.find(""))
print(s.find("xyz"))
print(s.find("xyz", 2))
print(s.find("o", -5))
print(s.find("o", -1))
print(s.find("o", -20))
print(s.find("o"))

u: str = "héllo wörld"
print(u.find("world", 3))
print(u.find("ö", 1))
print(u.find("ö", 2))
print(u.find("é", 1))
print(u.find("", 11))
print(u.find("", 12))
