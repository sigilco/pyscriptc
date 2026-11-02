"""A discriminated union over a Literal tag field."""

from typing import Literal

from pydantic import BaseModel, Field


class Cat(BaseModel):
    kind: Literal["cat"]
    lives: int


class Dog(BaseModel):
    kind: Literal["dog"]
    good: bool


class Owner(BaseModel):
    pet: Cat | Dog = Field(discriminator="kind")


o = Owner(pet={"kind": "dog", "good": True})
print(o.pet.kind)
