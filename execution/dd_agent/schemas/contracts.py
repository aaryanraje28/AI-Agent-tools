"""Schema for the contracts register (legal/contract review module)."""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, Field

from dd_agent.schemas.common import Confidence


class ContractFlag(BaseModel):
    description: str
    severity: str = Field(description="High | Medium | Low")


class ContractRegisterEntry(BaseModel):
    contract_id: str
    file_path: str
    counterparty: Optional[str] = None
    contract_type: Optional[str] = Field(default=None, description="e.g. MSA, lease, employment, license")
    effective_date: Optional[date] = None
    term_end_date: Optional[date] = None
    renewal_terms: Optional[str] = None
    termination_clause: Optional[str] = None
    change_of_control_clause: Optional[str] = None
    exclusivity_clause: Optional[str] = None
    indemnification_cap: Optional[str] = None
    governing_law: Optional[str] = None
    contract_value: Optional[str] = None
    signed: Optional[bool] = None
    flags: list[ContractFlag] = Field(default_factory=list)
    extraction_confidence: Confidence = Confidence.MEDIUM


class ContractsRegister(BaseModel):
    run_id: str
    entries: list[ContractRegisterEntry] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=datetime.utcnow)


# --- LLM extraction target (legal/contract_review.py) ---
# `contract_id` and `file_path` are attached by code from the document index entry, not by
# the model, so provenance is guaranteed rather than LLM-reported.


class ContractExtraction(BaseModel):
    is_contract: bool = Field(
        description="true only if this document is an actual bilateral/multilateral agreement "
        "(MSA, lease, employment agreement, license, NDA, etc.) — false for board resolutions, "
        "certificates, policies, or other non-contract legal documents"
    )
    counterparty: Optional[str] = None
    contract_type: Optional[str] = Field(default=None, description="e.g. MSA, lease, employment, license")
    effective_date: Optional[date] = None
    term_end_date: Optional[date] = None
    renewal_terms: Optional[str] = None
    termination_clause: Optional[str] = None
    change_of_control_clause: Optional[str] = None
    exclusivity_clause: Optional[str] = None
    indemnification_cap: Optional[str] = None
    governing_law: Optional[str] = None
    contract_value: Optional[str] = None
    signed: Optional[bool] = Field(
        default=None, description="true if signature blocks are filled/executed, false if visibly blank/unsigned"
    )
    flags: list[ContractFlag] = Field(
        default_factory=list,
        description="issues visible in the text itself, e.g. unusual liability terms, one-sided indemnification, "
        "missing standard clauses. Do not flag missing signature or expiry here — those are checked separately.",
    )
    extraction_confidence: Confidence = Confidence.MEDIUM
