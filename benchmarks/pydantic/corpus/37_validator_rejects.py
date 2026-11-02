"""A field_validator that rejects a value."""

from pydantic import BaseModel, ValidationError, field_validator


class Small(BaseModel):
    v: int

    @field_validator("v")
    @classmethod
    def positive(cls, v: int) -> int:
        if v < 0:
            raise ValueError("must be non-negative")
        return v


try:
    Small(v=-1)
except ValidationError as exc:
    print(exc.error_count())
