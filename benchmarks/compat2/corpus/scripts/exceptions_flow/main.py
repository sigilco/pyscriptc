def parse(s: str) -> int:
    try:
        return int(s)
    except ValueError:
        return -1


def divide(a: int, b: int) -> str:
    try:
        return str(a // b)
    except ZeroDivisionError:
        return "div0"
    finally:
        pass


print([parse(s) for s in ["12", "x", "-5", ""]])
print(divide(7, 2), divide(1, 0))
try:
    raise KeyError("missing")
except KeyError as err:
    print("caught", err)
