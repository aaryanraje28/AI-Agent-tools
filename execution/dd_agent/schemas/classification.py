"""Schema for a single document's classification result (LLM structured output target)."""
from __future__ import annotations

from pydantic import BaseModel, Field

from dd_agent.schemas.common import Confidence, DDCategory
from dd_agent.schemas.document_index import ExtractedEntity


class ClassificationResult(BaseModel):
    category: DDCategory
    confidence: Confidence
    key_entities: list[ExtractedEntity] = Field(default_factory=list)
    summary: str = Field(description="1-2 sentence summary of what the document is and contains")
