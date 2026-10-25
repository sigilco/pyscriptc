# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "tabulate>=0.9.0",
# ]
# ///
from __future__ import annotations

from typing import Any

import tabulate


def render_report(headers: list[str], rows: list[list[Any]]) -> str:
    rendered: str = tabulate.tabulate(rows, headers=headers, tablefmt="plain")
    return rendered


def render_grid(headers: list[str], rows: list[list[Any]]) -> str:
    rendered: str = tabulate.tabulate(rows, headers=headers, tablefmt="grid")
    return rendered


def main() -> None:
    headers: list[str] = ["Item", "Quantity", "Price"]
    rows: list[list[Any]] = [
        ["Widget A", 10, 2.5],
        ["Widget B", 5, 12.0],
        ["Widget C", 100, 0.75],
    ]
    plain_output: str = render_report(headers, rows)
    print("=== Plain Table ===")
    print(plain_output)
    grid_output: str = render_grid(headers, rows)
    print("=== Grid Table ===")
    print(grid_output)


if __name__ == "__main__":
    main()
