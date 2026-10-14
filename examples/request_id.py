"""Pull the first integer token out of one log line.

`request_id("id=88421 status=500")` returns `"88421"`. A line with no digits
returns an empty string.
"""

import re


def request_id(line: str) -> str:
    hits: list[str] = re.findall(r"\d+", line)
    if len(hits) == 0:
        return ""
    return hits[0]
