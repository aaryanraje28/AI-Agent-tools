"""Schema for the document index — one entry per source document in the data room."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from dd_agent.schemas.common import DDCategory, DocumentStatus


class ExtractedEntity(BaseModel):
    name: str
    type: str = Field(description="e.g. company, person, date, monetary_amount, jurisdiction")


class DocumentIndexEntry(BaseModel):
    doc_id: str = Field(description="stable id, e.g. sha1 of the relative file path")
    file_path: str = Field(description="path relative to the data room root")
    file_type: str = Field(description="pdf | docx | xlsx | image")
    category: DDCategory = DDCategory.UNCLASSIFIED
    classification_confidence: Optional[str] = None
    status: DocumentStatus = DocumentStatus.OK
    page_count: Optional[int] = None
    key_entities: list[ExtractedEntity] = Field(default_factory=list)
    summary: Optional[str] = Field(default=None, description="1-2 sentence summary of the document")
    text_cache_path: Optional[str] = Field(
        default=None, description="path under .tmp/<run_id>/text/ where extracted text is cached"
    )
    ocr_used: bool = False
    indexed_at: datetime = Field(default_factory=datetime.utcnow)
    error: Optional[str] = Field(default=None, description="extraction/classification error, if any")


class DocumentIndex(BaseModel):
    run_id: str
    dataroom_path: str
    entries: list[DocumentIndexEntry] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=datetime.utcnow)
