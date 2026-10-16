from pydantic import BaseModel, Field
from typing import Literal

class SearchBody(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=5, ge=1, le=20)


class AskBody(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=5, ge=1, le=20)


class RelatedBody(BaseModel):
    kind: Literal["teammates", "team", "policies", "roadmap_deals"]
    entity_id: str | None = Field(default=None, max_length=200)
    limit: int = Field(default=20, ge=1, le=25)
    quarter: str | None = Field(default=None, pattern=r"^\d{4}-Q[1-4]$")
    min_arr: int | None = Field(default=None, ge=0)


class TokenBody(BaseModel):
    persona: str = Field(min_length=1, max_length=64)