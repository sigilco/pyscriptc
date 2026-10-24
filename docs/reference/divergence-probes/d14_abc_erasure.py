from abc import ABC, abstractmethod

class Animal(ABC):
    @abstractmethod
    def speak(self) -> str:
        return "..."

class Dog(Animal):
    def speak(self) -> str:
        return "woof"

a: Animal = Dog()

def speak_of() -> str:
    return a.speak()

print(speak_of())
