class Box:
    real: int

    def __init__(self) -> None:
        self.real = 1

    def __getattr__(self, name: str) -> int:
        return 42

def read(b: Box) -> int:
    return b.missing

b: Box = Box()
print(b.real)
print(read(b))
