"""model_config with frozen=True rejecting mutation."""

from pydantic import BaseModel, ConfigDict


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)
    v: int


f = Frozen(v=1)
print(f.v)
