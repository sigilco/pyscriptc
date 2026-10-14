"""Admit one JSON access record from a small edge proxy.

The record is an object of strings, for example
`{"method":"GET","path":"/api/health","status":"200"}`.
`admit` returns 0 when the method is GET, the path starts with `/api`, and
the status is 200. Any other record returns 1. A missing field raises KeyError.
"""

import json


def admit(record: str) -> int:
    fields: dict[str, str] = json.loads(record)
    method: str = fields["method"]
    path: str = fields["path"]
    status: str = fields["status"]
    if method != "GET":
        return 1
    if not path.startswith("/api"):
        return 1
    if status != "200":
        return 1
    return 0
