"""model_validate from a dict."""

from pydantic import BaseModel


class Item(BaseModel):
    sku: str
    qty: int


item = Item.model_validate({"sku": "abc", "qty": 3})
print(item.sku, item.qty)
