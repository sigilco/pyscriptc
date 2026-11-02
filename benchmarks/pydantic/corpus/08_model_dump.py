"""model_dump and model_dump_json."""

from pydantic import BaseModel


class Item(BaseModel):
    sku: str
    qty: int


item = Item(sku="abc", qty=3)
print(item.model_dump())
print(item.model_dump_json())
