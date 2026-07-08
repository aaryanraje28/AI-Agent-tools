"""Schema for the consolidated risk register — the core DD output.

Every item must be traceable to at least one source document. This is what feeds the
"Key Risks & Red Flags" section of the report, so nothing here should be freeform or
unsourced.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from dd_agent.schemas.common import DDCategory, Confidence, Severity


class SourceReference(BaseModel):
    file: str = Field(description="path relative to the data room root")
    page: Optional[int] = Field(default=None, description="page number, if the source is paginated")
    excerpt: str = Field(description="verbatim snippet supporting the flag")


class RiskRegisterItem(BaseModel):
    id: str = Field(description="e.g. RISK-0001")
    category: DDCategory
    title: str = Field(description="short, analyst-style headline")
    description: str = Field(description="concise, no hedging language")
    severity: Severity
    confidence: Confidence
    module: str = Field(description="financial | legal | classification | manual")
    source_documents: list[SourceReference] = Field(min_length=1)
    recommended_question: str = Field(description="follow-up question for management")
    status: str = Field(default="open")
    date_identified: datetime = Field(default_factory=datetime.utcnow)


class RiskRegister(BaseModel):
    run_id: str
    items: list[RiskRegisterItem] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=datetime.utcnow)


# --- Internal to risk/risk_register.py's single batched LLM phrasing call ---
# `source_documents` deliberately never round-trips through the model: code attaches facts
# and sourcing deterministically (see RawFinding below); the model only phrases them into
# analyst-style prose. This guarantees nothing in the final register can be unsourced or
# mis-sourced by a model error.


class RawFinding(BaseModel):
    finding_id: str = Field(description="stable id used to re-merge the model's phrasing back onto this finding")
    category: DDCategory
    module: str = Field(description="financial | legal | classification")
    severity_hint: Severity
    raw_note: str = Field(description="deterministic, already-computed description of what was found")


class RiskDraft(BaseModel):
    finding_id: str
    title: str = Field(description="short, analyst-style headline")
    description: str = Field(description="concise, no hedging language")
    severity: Severity
    confidence: Confidence
    recommended_question: str = Field(description="follow-up question for management")


class RiskDraftBatch(BaseModel):
    items: list[RiskDraft]
