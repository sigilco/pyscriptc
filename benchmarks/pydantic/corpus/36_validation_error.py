"""A validation error is raised and carries errors()."""

from pydantic import BaseModel, ValidationError


class Rec(BaseModel):
    a: int


try:
    Rec(a="not-an-int")
except ValidationError as exc:
    print(exc.error_count())
