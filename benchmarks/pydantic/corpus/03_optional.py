"""Optional fields and None handling."""

from pydantic import BaseModel


class User(BaseModel):
    name: str
    email: str | None = None


print(User(name="a").model_dump())
print(User(name="a", email="e@x").email)
