class B:
    x: int

    def __init__(self) -> None:
        self.x = 1


class C(B):
    def __init__(self) -> None:
        pass


c: C = C()
print(c.x)
