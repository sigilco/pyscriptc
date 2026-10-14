def clamp(x: int) -> int:
    try:
        if x < 0:
            raise ValueError("neg")
        return x
    except ValueError:
        return 0
