"""An openai-style tool-call model with Optional fields and defaults."""

from pydantic import BaseModel


class Function(BaseModel):
    name: str
    arguments: str


class ToolCall(BaseModel):
    id: str
    type: str = "function"
    function: Function


tc = ToolCall(id="call_1", function=Function(name="get", arguments="{}"))
print(tc.type, tc.function.name)
