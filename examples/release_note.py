"""Write the end date of a release window and read it back.

`stamp` parses an ISO date, adds a whole number of days, writes that date to
`dest`, and returns the text read back.
"""

import datetime
import pathlib


def stamp(day: str, extra_days: int, dest: str) -> str:
    start: datetime.date = datetime.date.fromisoformat(day)
    end: datetime.date = start + datetime.timedelta(extra_days, 0, 0)
    text: str = end.isoformat()
    path: pathlib.Path = pathlib.Path(dest)
    path.write_text(text)
    return path.read_text()
