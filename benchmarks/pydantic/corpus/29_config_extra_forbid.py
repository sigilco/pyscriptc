"""model_config with extra='forbid' rejecting unknown keys."""

from pydantic import BaseModel, ConfigDict


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")
    a: int


s = Strict(a=1)
print(s.model_dump())
