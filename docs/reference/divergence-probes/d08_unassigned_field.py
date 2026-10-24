class P:
    x: int

    def __init__(self) -> None:
        self.x = 1

class C(P):
    def __init__(self) -> None:
        pass

def getx(c: C) -> int:
    return c.x

c: C = C()
print(getx(c))
