class C:
    @classmethod
    def make(cls) -> int:
        return 1

    @staticmethod
    def ping() -> int:
        return 2

print(C.make())
print(C.ping())
