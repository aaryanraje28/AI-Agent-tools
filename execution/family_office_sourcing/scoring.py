"""Deterministic priority tiering. No LLM involved — every point in the score traces to a
config-defined weight and a fact already present on the record, so a re-run with the same
inputs always produces the same tier (unlike anything phrased by a model).
"""
from __future__ import annotations

from family_office_sourcing.config import TIER1_CITIES, FamilyOfficeSourcingConfig
from family_office_sourcing.schemas import CandidateRecord, Confidence, InvestmentMode, PriorityTier


def _evidence_text(record: CandidateRecord) -> str:
    parts = []
    for source in record.sources:
        parts.append(source.short_description or "")
        parts.append(source.evidence_excerpt or "")
    return " ".join(parts).lower()


def _classify_investment_mode(text: str, config: FamilyOfficeSourcingConfig) -> InvestmentMode:
    """Purely additive metadata — matched independently of exclusion/tiering, since knowing
    a family office is an LP-style allocator vs. a direct co-investor is useful even for a
    Tier A confirmed record. Most descriptions won't clearly signal either and stay UNKNOWN
    rather than guessing from thin evidence."""
    is_lp = any(kw.lower() in text for kw in config.lp_allocator_keywords)
    is_direct = any(kw.lower() in text for kw in config.direct_investor_keywords)
    if is_lp and not is_direct:
        return InvestmentMode.LP_ALLOCATOR
    if is_direct and not is_lp:
        return InvestmentMode.DIRECT_COINVESTOR
    return InvestmentMode.UNKNOWN


def _office_type_exclusion(record: CandidateRecord, text: str, config: FamilyOfficeSourcingConfig) -> str | None:
    """Only applied to unconfirmed candidates — see config.yaml's comment on this list for
    why a Tracxn-confirmed record must never be re-filtered by its own description text."""
    if record.confidence == Confidence.CONFIRMED:
        return None
    for keyword in config.exclude_office_type_keywords:
        if keyword.lower() in text:
            return f"Evidence text matches non-family-office pattern: {keyword!r}"
    return None


def score_candidates(records: list[CandidateRecord], config: FamilyOfficeSourcingConfig) -> list[CandidateRecord]:
    weights = config.scoring
    for record in records:
        text = _evidence_text(record)
        record.investment_mode = _classify_investment_mode(text, config)
        record.exclusion_reason = _office_type_exclusion(record, text, config)

        # An unconfirmed candidate that enrichment judged implausible, or that matches a
        # non-family-office exclusion pattern, is noise, not a low-priority prospect — pin
        # it to the bottom regardless of other signals.
        if record.exclusion_reason is not None:
            record.priority_tier = PriorityTier.C
            record.priority_score = 0
            continue
        if record.enrichment is not None and not record.enrichment.is_plausible_family_office:
            record.priority_tier = PriorityTier.C
            record.priority_score = 0
            continue

        score = 0
        matched_sectors = sorted({s for s in config.target_sectors if s.lower() in text})
        record.sector_signals = matched_sectors

        # Trust comes from either a curated database (Tracxn) or an LLM-verified plausible
        # signal (e.g. a LinkedIn/web search hit enrichment.py assessed as genuine) — not
        # from Tracxn specifically. Without this, a search-discovery-only run (no Tracxn
        # input at all) could never produce a Tier A candidate no matter how strong the
        # evidence, which defeats the point of using search discovery as a primary source
        # rather than a minor supplement.
        if record.confidence == Confidence.CONFIRMED:
            score += weights.confirmed_source_weight
        elif record.enrichment is not None and record.enrichment.is_plausible_family_office:
            score += weights.enrichment_confirmed_weight
        if matched_sectors:
            score += weights.sector_fit_weight
        if record.city and record.city.lower() in TIER1_CITIES:
            score += weights.tier1_city_weight
        if any(s.has_recent_investment for s in record.sources):
            score += weights.recent_activity_weight

        record.priority_score = score
        if score >= weights.tier_a_min_score:
            record.priority_tier = PriorityTier.A
        elif score >= weights.tier_b_min_score:
            record.priority_tier = PriorityTier.B
        else:
            record.priority_tier = PriorityTier.C
    return records
