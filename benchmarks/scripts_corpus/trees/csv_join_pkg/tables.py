"""Reading and writing the CSV rows."""

from pathlib import Path

NEWLINE = "\n"


def read_csv(path: str) -> list[list[str]]:
    """Parse a small CSV file into rows of strings."""
    rows: list[list[str]] = []
    text: str = Path(path).read_text()
    for line in text.split(NEWLINE):
        if line:
            rows.append(line.split(","))
    return rows


def write_rows(rows: list[list[str]]) -> None:
    """Print every row, comma separated."""
    for row in rows:
        print(",".join(row))
