"""datetime.date plus timedelta, then toordinal."""

import datetime


def bench(n: int) -> int:
    total: int = 0
    i: int = 0
    while i < n:
        step: int = i - (i // 28) * 28
        day: datetime.date = datetime.date(2020, 1, 1) + datetime.timedelta(step, 0, 0)
        total = total + day.toordinal()
        i = i + 1
    return total
