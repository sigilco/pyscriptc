"""A dict of nested models."""

from pydantic import BaseModel


class Item(BaseModel):
    sku: str


class Catalog(BaseModel):
    items: dict[str, Item]


c = Catalog.model_validate({"items": {"a": {"sku": "x"}}})
print(c.items["a"].sku)
