"""An openai-style chat completion response model."""

from pydantic import BaseModel


class CompletionUsage(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class ChatCompletionMessage(BaseModel):
    role: str
    content: str | None = None


class Choice(BaseModel):
    finish_reason: str
    index: int
    message: ChatCompletionMessage


class ChatCompletion(BaseModel):
    id: str
    model: str
    choices: list[Choice]
    usage: CompletionUsage | None = None


c = ChatCompletion(
    id="cmpl-1",
    model="gpt-4o",
    choices=[Choice(finish_reason="stop", index=0, message=ChatCompletionMessage(role="assistant", content="hi"))],
    usage=CompletionUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
)
print(c.choices[0].message.content, c.usage.total_tokens)
