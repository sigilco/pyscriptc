"""A langchain-style tool arg model and its JSON schema."""

from pydantic import BaseModel, Field


class WeatherInput(BaseModel):
    location: str = Field(description="city name")
    unit: str = Field(default="celsius", description="temperature unit")


schema = WeatherInput.model_json_schema()
print(schema["title"], sorted(schema["properties"]))
