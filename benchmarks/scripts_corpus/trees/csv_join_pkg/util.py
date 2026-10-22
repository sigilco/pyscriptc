"""Small shared helpers for the join."""


def clamp(value: int, low: int, high: int) -> int:
    """Keep value inside the range."""
    if value < low:
        return low
    if value > high:
        return high
    return value


def label(pair: tuple[str, str]) -> str:
    """Render a pair as KEY=VALUE, for debug output."""
    return f"{pair[0]}={pair[1]}"
