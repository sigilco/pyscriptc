"""A langchain-style tool args model validated from a JSON blob."""

from pydantic import BaseModel


class SearchArgs(BaseModel):
    query: str
    limit: int = 5


args = SearchArgs.model_validate_json('{"query": "pydantic"}')
print(args.query, args.limit)
