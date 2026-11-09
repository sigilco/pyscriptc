class P:
    def __setattr__(self, name: str, value: int) -> None:
        print("set", name)


p: P = P()
p.x = 1
