"""An openai-style response with a list of nested models and literals."""

from typing import Literal

from pydantic import BaseModel


class Logprob(BaseModel):
    token: str
    logprob: float


class ChoiceLogprobs(BaseModel):
    content: list[Logprob] | None = None


class Choice(BaseModel):
    finish_reason: Literal["stop", "length", "tool_calls"]
    logprobs: ChoiceLogprobs | None = None


c = Choice(finish_reason="stop", logprobs=ChoiceLogprobs(content=[Logprob(token="a", logprob=-0.5)]))
print(c.finish_reason, c.logprobs.content[0].token)
