"""A list of nested models, dumped and validated back."""

from pydantic import BaseModel


class Item(BaseModel):
    sku: str


class Order(BaseModel):
    items: list[Item]


o = Order.model_validate({"items": [{"sku": "a"}, {"sku": "b"}]})
print([i.sku for i in o.items])
