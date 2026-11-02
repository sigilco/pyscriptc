"""BaseModel with defaults and Optional defaults."""

from pydantic import BaseModel


class Config(BaseModel):
    name: str
    retries: int = 3
    verbose: bool = False
    note: str | None = None


c = Config(name="svc")
print(c.model_dump())
print(Config(name="x", retries=9, verbose=True, note="n").model_dump())
