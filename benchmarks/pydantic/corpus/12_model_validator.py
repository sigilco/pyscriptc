"""A model_validator that checks a cross-field invariant."""

from pydantic import BaseModel, model_validator


class Range(BaseModel):
    lo: int
    hi: int

    @model_validator(mode="after")
    def check(self):
        if self.lo > self.hi:
            raise ValueError("lo must not exceed hi")
        return self


print(Range(lo=1, hi=2).model_dump())
