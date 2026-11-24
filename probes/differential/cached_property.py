from functools import cached_property


class Report:
    rows: int
    label: str

    def __init__(self, rows: int, label: str) -> None:
        self.rows = rows
        self.label = label

    @cached_property
    def summary(self) -> str:
        print("computing summary")
        return f"{self.label}:{self.rows}"

    @cached_property
    def doubled(self) -> int:
        print("computing doubled")
        return self.rows * 2


class Extended(Report):
    @cached_property
    def tripled(self) -> int:
        print("computing tripled")
        return self.rows * 3


one = Report(2, "a")
two = Report(3, "b")

print(one.summary, one.summary)
print(one.doubled, two.doubled)
print(two.summary, two.summary)

ext = Extended(4, "c")
print(ext.summary, ext.tripled, ext.doubled, ext.summary)
