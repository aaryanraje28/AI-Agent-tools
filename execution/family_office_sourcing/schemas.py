"""Schemas for the family office sourcing pipeline.

Two layers: `Candidate` is one raw hit from one source (Tracxn / SEBI AIF / web search),
kept exactly as evidence. `CandidateRecord` is what `matching.py` merges same-office
candidates into — the unit the rest of the pipeline (enrichment, scoring, output) operates
on. Every `CandidateRecord` must carry at least one `Candidate` in `sources`, so every field
in the final report can be traced back to where it came from (per the directive's "nothing
asserted without a citation" rule).
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class SourceType(str, Enum):
    TRACXN = "tracxn"
    SEBI_AIF = "sebi_aif"
    WEB_SIGNAL = "web_signal"


class Confidence(str, Enum):
    CONFIRMED = "confirmed"     # directly tagged as a family office by a structured source (Tracxn)
    UNCONFIRMED = "unconfirmed"  # inferred from a signal (SEBI sponsor name, news/LinkedIn mention)


class PriorityTier(str, Enum):
    A = "A"
    B = "B"
    C = "C"
    UNSCORED = "Unscored"


class InvestmentMode(str, Enum):
    """Deterministic keyword classification (scoring.py), not a query filter on any source —
    Tracxn/LinkedIn don't expose this as a structured field. Most candidates' evidence text
    won't clearly signal either, and stay UNKNOWN rather than being guessed."""

    LP_ALLOCATOR = "LP/Allocator"          # invests as a limited partner into other funds
    DIRECT_COINVESTOR = "Direct/Co-Investor"  # invests directly into companies/deals
    UNKNOWN = "Unknown"


class Candidate(BaseModel):
    """One raw hit from one source. Never mutated after ingest — `matching.py` merges
    references to these, it doesn't edit them."""

    source_type: SourceType
    source_name: str = Field(description="human-readable source label, e.g. 'Tracxn: Single Family Offices feed'")
    source_url: Optional[str] = None
    raw_name: str
    confidence: Confidence
    city: Optional[str] = None
    state: Optional[str] = None
    principal: Optional[str] = Field(default=None, description="named individual/family, only if stated in the source")
    short_description: Optional[str] = None
    website: Optional[str] = None
    has_phone_on_file: Optional[bool] = None
    has_recent_investment: Optional[bool] = Field(default=None, description="source reports an active/recent investment track record, if known")
    external_id: Optional[str] = Field(default=None, description="source-native ID, e.g. Tracxn investorId")
    evidence_excerpt: Optional[str] = Field(default=None, description="verbatim snippet supporting inclusion (required for web_signal/sebi_aif)")
    retrieved_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class EnrichmentResult(BaseModel):
    """LLM plausibility assessment — only produced for `unconfirmed` candidates. Never
    contributes new facts (names, contacts) not already present in the source evidence."""

    is_plausible_family_office: bool
    rationale: str = Field(description="one or two sentences, grounded in the evidence_excerpt")


# --- Internal to enrichment.py's single batched LLM call ---


class EnrichmentRequest(BaseModel):
    candidate_id: str
    raw_name: str
    short_description: Optional[str] = None
    evidence_excerpt: Optional[str] = None


class EnrichmentDraft(BaseModel):
    candidate_id: str
    is_plausible_family_office: bool
    rationale: str


class EnrichmentBatch(BaseModel):
    items: list[EnrichmentDraft]


class CandidateRecord(BaseModel):
    """One real-world family office, after cross-source dedupe."""

    candidate_id: str = Field(description="stable hash of the normalized display name")
    display_name: str
    aliases: list[str] = Field(default_factory=list)
    city: Optional[str] = None
    state: Optional[str] = None
    principal: Optional[str] = None
    sector_signals: list[str] = Field(default_factory=list)
    website: Optional[str] = None
    confidence: Confidence
    enrichment: Optional[EnrichmentResult] = None
    investment_mode: InvestmentMode = InvestmentMode.UNKNOWN
    exclusion_reason: Optional[str] = Field(
        default=None,
        description="set when evidence text matches a non-family-office pattern (VC firm, bank, "
        "wealth manager, advisory firm) — only applied to unconfirmed candidates, since Tracxn's "
        "Single Family Offices feed is already curated and shouldn't be re-filtered by its own "
        "description text (see directive learnings on Brescon/Goel Family Office false-positive risk)",
    )
    sources: list[Candidate] = Field(min_length=1)
    priority_tier: PriorityTier = PriorityTier.UNSCORED
    priority_score: int = 0
    last_refreshed: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class SourcingRun(BaseModel):
    run_id: str
    records: list[CandidateRecord] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
