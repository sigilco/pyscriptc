class Temperature:
    _celsius: float

    def __init__(self, celsius: float) -> None:
        self._celsius = celsius

    @property
    def celsius(self) -> float:
        return self._celsius

    @celsius.setter
    def celsius(self, value: float) -> None:
        self._celsius = value

    @property
    def fahrenheit(self) -> float:
        return self._celsius * 9.0 / 5.0 + 32.0


class Labeled:
    _celsius: float
    label: str

    def __init__(self, celsius: float, label: str) -> None:
        self._celsius = celsius
        self.label = label

    @property
    def celsius(self) -> float:
        return self._celsius

    @celsius.setter
    def celsius(self, value: float) -> None:
        self._celsius = value * 2.0


t = Temperature(20.0)
print(t.celsius, t.fahrenheit)
t.celsius = 100.0
print(t.celsius, t.fahrenheit)
labeled = Labeled(-40.0, "cold")
print(labeled.label, labeled.celsius)
labeled.celsius = 10.0
print(labeled.label, labeled.celsius)
x = 5.0
t.celsius = x + 1.5
print(t.celsius, t.fahrenheit)
