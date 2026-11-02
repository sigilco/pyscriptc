"""model_validate_json from a JSON string."""

from pydantic import BaseModel


class Item(BaseModel):
    sku: str
    qty: int


item = Item.model_validate_json('{"sku": "abc", "qty": 3}')
print(item.sku, item.qty)
