def write_it(p: str, s: str) -> None:
    with open(p, "w") as fh:
        fh.write(s)

def read_it(p: str) -> str:
    with open(p, "r") as fh:
        return fh.read()

write_it("d39.txt", "a\r\nb\rc")
s: str = read_it("d39.txt")
print(s.encode("utf-8"))
