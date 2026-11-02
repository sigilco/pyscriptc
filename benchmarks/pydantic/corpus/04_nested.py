"""Nested BaseModel fields."""

from pydantic import BaseModel


class Address(BaseModel):
    city: str
    zip: str


class Person(BaseModel):
    name: str
    address: Address


p = Person(name="ada", address=Address(city="london", zip="e1"))
print(p.address.city, p.name)
print(p.model_dump())
