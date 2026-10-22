"""Reading the log file."""

from pathlib import Path

NEWLINE = "\n"


def read_lines(path: str) -> list[str]:
    """Every non-empty line of the file."""
    lines: list[str] = []
    text: str = Path(path).read_text()
    for line in text.split(NEWLINE):
        if line:
            lines.append(line)
    return lines
