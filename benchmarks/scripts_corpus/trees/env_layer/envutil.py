"""Render the environment report."""

SEPARATOR = "="


def report(names: list[str], prefix: str) -> list[str]:
    """One line per name, with a marker when the value is hidden."""
    lines: list[str] = []
    for name in names:
        lines.append(f"{name}{SEPARATOR}<set>")
    if not lines:
        lines.append(f"no variable starts with {prefix}")
    return lines
