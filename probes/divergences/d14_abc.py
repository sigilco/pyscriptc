from abc import ABC, abstractmethod


class A(ABC):
    @abstractmethod
    def f(self) -> int:
        return 1


a: A = A()
print(a.f())
