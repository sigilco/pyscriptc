# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "python-dateutil>=2.9.0",
# ]
# ///
from __future__ import annotations

import datetime

from dateutil import parser
from dateutil.relativedelta import relativedelta


def parse_timestamp(iso_string: str) -> datetime.datetime:
    dt: datetime.datetime = parser.parse(iso_string)
    return dt


def add_business_offset(dt: datetime.datetime, days: int, months: int) -> datetime.datetime:
    delta: relativedelta = relativedelta(days=days, months=months)
    return dt + delta


def main() -> None:
    raw_date: str = "2026-10-25T04:30:00Z"
    parsed: datetime.datetime = parse_timestamp(raw_date)
    print("Parsed year:", parsed.year)
    print("Parsed month:", parsed.month)
    print("Parsed day:", parsed.day)

    target: datetime.datetime = add_business_offset(parsed, days=5, months=1)
    print("Target year:", target.year)
    print("Target month:", target.month)
    print("Target day:", target.day)


if __name__ == "__main__":
    main()
