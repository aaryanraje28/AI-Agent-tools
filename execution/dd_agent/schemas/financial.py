"""Schema for the normalized financial summary (financial DD module)."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class PeriodFigure(BaseModel):
    period_label: str = Field(description="e.g. FY22, FY23, FY24")
    revenue: Optional[float] = None
    ebitda_reported: Optional[float] = None
    ebitda_adjusted: Optional[float] = None
    ebitda_adjustments: list[str] = Field(default_factory=list, description="one-off items normalized out")
    net_working_capital: Optional[float] = None
    total_debt: Optional[float] = None
    related_party_revenue: Optional[float] = None
    related_party_expense: Optional[float] = None
    source_file: Optional[str] = None


class FinancialAnomaly(BaseModel):
    description: str
    severity: str = Field(default="Medium", description="High | Medium | Low")
    period_label: Optional[str] = None
    source_file: Optional[str] = None


class FinancialSummary(BaseModel):
    run_id: str
    periods: list[PeriodFigure] = Field(default_factory=list)
    anomalies: list[FinancialAnomaly] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=datetime.utcnow)


# --- LLM extraction target (financial/parser.py) ---
# Mirrors PeriodFigure minus `source_file`, which is attached by code (not the model) so
# every figure's provenance is guaranteed rather than LLM-reported.


class ExtractedPeriodFigure(BaseModel):
    period_label: str = Field(description="e.g. FY22, FY23, FY24 — as labeled in the source document")
    revenue: Optional[float] = None
    ebitda_reported: Optional[float] = None
    ebitda_adjusted: Optional[float] = Field(
        default=None, description="EBITDA after normalizing out one-off items, if determinable"
    )
    ebitda_adjustments: list[str] = Field(
        default_factory=list, description="one-off items normalized out, e.g. 'one-time legal settlement'"
    )
    net_working_capital: Optional[float] = None
    total_debt: Optional[float] = None
    related_party_revenue: Optional[float] = None
    related_party_expense: Optional[float] = None


class FinancialExtractionResult(BaseModel):
    periods: list[ExtractedPeriodFigure] = Field(default_factory=list)
