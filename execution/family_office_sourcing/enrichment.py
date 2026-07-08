"""Plausibility assessment for `unconfirmed` candidates only (SEBI AIF sponsors, web/
LinkedIn signals). Tracxn-sourced (`confirmed`) records already come from a curated
database and skip this entirely — no LLM call needed to confirm what's already confirmed.

One batched Claude call for the whole run, not one per candidate, per the same reasoning as
dd_agent's risk register phrasing: fewer calls, and `candidate_id`-based matching means a
model error can only affect the rationale text, never which candidate a fact is attached to.
"""
from __future__ import annotations

import logging

from dd_agent.config import ModelConfig
from dd_agent.llm.claude_client import structured_call
from family_office_sourcing.schemas import (
    CandidateRecord,
    Confidence,
    EnrichmentBatch,
    EnrichmentRequest,
    EnrichmentResult,
)

logger = logging.getLogger("family_office_sourcing.enrichment")

_SYSTEM_PROMPT = """You are assessing whether open-source signals (SEBI AIF sponsor names, \
web/LinkedIn search hits) actually indicate an Indian family office (a private investment \
vehicle for a single wealthy family or individual), as opposed to noise (a corporate \
investor, a professional third-party fund manager, an unrelated company, or a person with \
no evident wealth/investment vehicle).

Base your judgment ONLY on the raw_name, short_description, and evidence_excerpt provided \
for each item — never assume facts not present in that text. If the evidence is ambiguous, \
say so in the rationale and lean toward is_plausible_family_office=false; a false negative \
costs one manual review, a false positive wastes outreach effort on the wrong prospect."""


def enrich_unconfirmed(records: list[CandidateRecord], model_config: ModelConfig) -> list[CandidateRecord]:
    unconfirmed = [r for r in records if r.confidence == Confidence.UNCONFIRMED]
    if not unconfirmed:
        logger.info("No unconfirmed candidates to enrich")
        return records

    requests = [
        EnrichmentRequest(
            candidate_id=r.candidate_id,
            raw_name=r.display_name,
            short_description=r.sources[0].short_description,
            evidence_excerpt=r.sources[0].evidence_excerpt,
        )
        for r in unconfirmed
    ]
    user_prompt = (
        "Assess each candidate below and return one EnrichmentDraft per candidate_id "
        "(same candidate_id, do not invent new ones):\n\n"
        + "\n".join(req.model_dump_json() for req in requests)
    )

    try:
        batch = structured_call(_SYSTEM_PROMPT, user_prompt, EnrichmentBatch, model_config)
    except Exception as exc:  # noqa: BLE001
        # Unlike dd_agent's classification step (a required stage the whole report depends
        # on), enrichment here only adds a plausibility note to a handful of unconfirmed
        # candidates — the confirmed-source majority of the run is already valid without
        # it. A billing/auth/network failure mid-call (observed live: a $0 credit balance
        # surfaces as anthropic.BadRequestError, not StructuredCallError) must not discard
        # an otherwise-successful sourcing run. Caught during the first live smoke test.
        logger.warning("Enrichment call failed, proceeding without plausibility assessment: %s", exc)
        return records

    drafts_by_id = {d.candidate_id: d for d in batch.items}
    for record in unconfirmed:
        draft = drafts_by_id.get(record.candidate_id)
        if draft is None:
            logger.warning("No enrichment draft returned for candidate %s", record.candidate_id)
            continue
        record.enrichment = EnrichmentResult(
            is_plausible_family_office=draft.is_plausible_family_office,
            rationale=draft.rationale,
        )
    return records
