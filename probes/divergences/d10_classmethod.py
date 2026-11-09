class K:
    total: int = 3

    @classmethod
    def get(cls) -> int:
        return cls.total


print(K.get())
