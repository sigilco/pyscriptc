# Issue #1424: bytes methods still use str semantics. The probe runs on the
# pinned CPython oracle and on the compiled program: the stdout, stderr and
# exit code must match byte for byte. Every row of the issue table is here,
# plus the neighbouring seams (find_from, rindex, split, strip, the is*
# predicates over mixed payloads and int(bytes) forms). Exceptions are
# caught and reported so one bad row does not mask the rest.
from collections.abc import Callable

data: bytes = b"caf\xc3\xa9 xyz"


def show(label: str, fn: Callable[[], object]) -> None:
    try:
        print(label, fn())
    except ValueError as exc:
        print(label, "ValueError", str(exc))


# find / rfind / index count bytes, not code points (issue row 1).
print(data.find(b"z"), data.rfind(b"z"), data.index(b"z"))
print(data.find(b"\xa9"), data.rfind(b"caf"), data.index(b" "))
print(data.find(b"q"), data.rfind(b"q"), data.index(b"a"))
print(data.find(b"z", 4), data.find(b"z", 9))

# count of the empty needle: bytes + 1 (issue row 2).
print(data.count(b""), data.count(b"a"), data.count(b"\xc3"))
print(b"".count(b""), b"abc".count(b"b"), data.count(b"\xc3\xa9"))

# upper / lower are ASCII-only (issue rows 3-4).
print(b"a\xc3\xa9a".upper(), b"A\xc3\x89A".lower(), b"abc".upper(), b"ABC".lower())

# isalpha / isalnum / isspace are ASCII-only (issue rows 5-7).
print(b"a\xc3\xa9a".isalpha(), b"abc".isalpha(), b"".isalpha())
print(b"\xc3\xa9".isalnum(), b"a1".isalnum(), b"1".isalnum())
print(b"\xc2\xa0".isspace(), b" ".isspace(), b"".isspace())
print(b"a\xc3\xa9".isupper(), b"ABC".isupper(), b"abc".islower())

# partition / rpartition return bytes parts (issue row 8).
print(data.partition(b" "))
print(data.rpartition(b" "))
print(b"abc".partition(b"q"))
print(b"abc".rpartition(b"q"))

# int(bytes) strips ASCII whitespace only (issue row 9).
show("int", lambda: int(b"\xa0 5"))
show("int", lambda: int(b"  42"))
show("int", lambda: int(b"-7"))
show("int", lambda: int(b"1_0"))
show("int", lambda: int(b"1 0"))
show("int", lambda: int(b"\t\n12\r"))
