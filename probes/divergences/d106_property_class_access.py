class Box:
    _n: int

    def __init__(self, n: int) -> None:
        self._n = n

    @property
    def n(self) -> int:
        return self._n


print(type(Box.n).__name__)
