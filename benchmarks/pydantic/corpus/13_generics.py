"""A generic BaseModel parametrized over a type variable."""

from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class Box(BaseModel, Generic[T]):
    value: T


b = Box[int](value=3)
print(b.value, b.model_dump())
