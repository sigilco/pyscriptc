from dataclasses import dataclass


class Base:
    KIND = "base"
    N: int = 1


class C(Base):
    LIMIT: int = 3
    NAME = "x"
    NEG = -4
    EXPR = 1 + 2
    CAT = "a" + "b"
    FLAG = True
    PI = 2.5
    OTHER = Base.N + 1
    OPT: int | None = None

    def describe(self) -> str:
        return f"{self.NAME}:{self.LIMIT}:{C.NEG}:{C.EXPR}"


@dataclass
class D:
    a: int = 7
    b: str = "hi"


print(C.LIMIT, C.NAME, C.NEG, C.EXPR, C.CAT, C.FLAG, C.PI, C.OTHER)
print(C().LIMIT, C().NAME, C().KIND, C().N, C().OPT is None)
print(C.OPT is None, C().describe())
print(Base.KIND, Base.N, D.a, D.b, D(1).a, D(1, "z").b)
