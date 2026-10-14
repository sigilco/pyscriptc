"""Bill a usage meter and total an invoice in integer cents.

`charge` returns `(used + extra) * rate`. `total` sums the line items and adds
a floor-divided tax. A negative line is rejected and `total` returns -1.
"""


class Meter:
    used: int

    def __init__(self, used: int) -> None:
        self.used = used

    def add(self, more: int) -> None:
        self.used = self.used + more

    def bill(self, cents_per_unit: int) -> int:
        return self.used * cents_per_unit


def charge(used: int, extra: int, rate: int) -> int:
    meter: Meter = Meter(used)
    meter.add(extra)
    return meter.bill(rate)


def line_ok(cents: int) -> int:
    try:
        if cents < 0:
            raise ValueError("negative line")
        return cents
    except ValueError:
        return -1


def total(cents: list[int], tax_percent: int) -> int:
    net: int = 0
    for item in cents:
        checked: int = line_ok(item)
        if checked < 0:
            return -1
        net = net + checked
    tax: int = net * tax_percent // 100
    return net + tax


def invoice(tax_percent: int) -> int:
    """Three stocked lines: 1000, 2500, and 400 cents, plus tax."""
    lines: list[int] = [1000, 2500, 400]
    return total(lines, tax_percent)


def bad_invoice() -> int:
    """A negative line rejects the invoice."""
    lines: list[int] = [1000, -1]
    return total(lines, 10)
