"""A generic model with a nested generic field."""

from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int


p = Page[str](items=["a", "b"], total=2)
print(p.total, p.items)
