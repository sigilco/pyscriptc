"""An anthropic-style discriminated content block."""

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field


class TextBlock(BaseModel):
    type: Literal["text"] = "text"
    text: str


class ToolUseBlock(BaseModel):
    type: Literal["tool_use"]
    id: str
    name: str


Block = Annotated[Union[TextBlock, ToolUseBlock], Field(discriminator="type")]


class Message(BaseModel):
    content: list[Block]


m = Message(content=[{"type": "text", "text": "hi"}, {"type": "tool_use", "id": "t1", "name": "f"}])
print(m.content[0].text, m.content[1].name)
