flag: list[int] = [0]


def side() -> bool:
    flag[0] += 1
    return True


assert side()
print(flag[0])
