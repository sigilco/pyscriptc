def point(cr: float, ci: float, limit: int) -> int:
    zr = 0.0
    zi = 0.0
    for i in range(limit):
        zr2 = zr * zr
        zi2 = zi * zi
        if zr2 + zi2 > 4.0:
            return i
        zi = 2.0 * zr * zi + ci
        zr = zr2 - zi2 + cr
    return limit


def main() -> None:
    total = 0
    for row in range(24):
        line = ""
        for col in range(64):
            n = point(-2.0 + col * 3.0 / 64, -1.2 + row * 2.4 / 24, 50)
            total += n
            line += "#" if n == 50 else ("+" if n > 8 else ".")
        print(line)
    print(total)


main()
