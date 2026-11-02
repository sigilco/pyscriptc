"""An anthropic-style usage block."""

from pydantic import BaseModel


class Usage(BaseModel):
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


u = Usage(input_tokens=10, output_tokens=20)
print(u.input_tokens, u.output_tokens, u.cache_read_input_tokens)
