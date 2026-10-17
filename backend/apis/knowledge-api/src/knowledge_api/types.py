"""Request bodies for the REST adapter. Retrieval itself lives in gateway-core."""

from typing import Literal

from pydantic import BaseModel, Field


class SearchBody(BaseModel):
    """Hybrid search over authorized chunks. The caller does not name a source."""

    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=5, ge=1, le=20)


class AskBody(BaseModel):
    """Natural-language question. The router picks search, a graph template, or both."""

    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=5, ge=1, le=20)


class RelatedBody(BaseModel):
    """Bounded graph lookup. kind is one of four templates; there is no open Cypher."""

    kind: Literal["teammates", "team", "policies", "roadmap_deals"]
    entity_id: str | None = Field(default=None, max_length=200)
    limit: int = Field(default=20, ge=1, le=25)
    quarter: str | None = Field(default=None, pattern=r"^\d{4}-Q[1-4]$")
    min_arr: int | None = Field(default=None, ge=0)


class TokenBody(BaseModel):
    """Dev-only persona name. The route is closed unless DEV_TOKEN_ENDPOINT=1."""

    persona: str = Field(min_length=1, max_length=64)