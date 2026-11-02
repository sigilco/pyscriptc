"""TypeAdapter validating a bare list and a bare dict type."""

from pydantic import TypeAdapter

adapter = TypeAdapter(list[int])
print(adapter.validate_python([1, 2, 3]))
dadapter = TypeAdapter(dict[str, int])
print(dadapter.validate_python({"a": 1}))
