"""Consolidates findings from all modules into the single risk register.

Facts and sourcing are collected deterministically in code — financial anomalies (from
financial/analysis.py), contract flags (from legal/contract_review.py), and DD category
coverage gaps (a category with zero documents is itself a finding, per the directive).
Those raw findings are sent to Claude in a single batched call to be phrased into
analyst-style title/description/recommended_question (schema:
dd_agent.schemas.risk_register.RiskDraftBatch); `source_documents` is attached by this
code afterward and never touched by the model, so nothing in the register can be
unsourced or mis-sourced by a model error. If the phrasing call fails, findings still make
it into the register using their raw (less polished) description rather than being
dropped — see the fallback in the merge loop below.
"""
from __future__ import annotations

import json
import logging

from dd_agent.config import ModelConfig
from dd_agent.llm.claude_client import StructuredCallError, structured_call
from dd_agent.schemas.common import Confidence, DDCategory, Severity
from dd_agent.schemas.contracts import ContractsRegister
from dd_agent.schemas.document_index import DocumentIndex
from dd_agent.schemas.financial import FinancialSummary
from dd_agent.schemas.risk_register import (
    RawFinding,
    RiskDraftBatch,
    RiskRegister,
    RiskRegisterItem,
    SourceReference,
)

logger = logging.getLogger("dd_agent.risk.risk_register")

_SEVERITY_RANK = {Severity.HIGH: 0, Severity.MEDIUM: 1, Severity.LOW: 2}


def _coerce_severity(value: str, default: Severity = Severity.MEDIUM) -> Severity:
    try:
        return Severity(value)
    except ValueError:
        return default

_SYSTEM_PROMPT = """You are a due diligence analyst preparing a risk register for a deal \
partner. For each raw finding provided (already fact-checked and sourced by upstream \
tooling — do not second-guess the underlying facts), write:
- title: a short, analyst-style headline
- description: concise, factual, no hedging language ("appears to", "may possibly")
- severity: High, Medium, or Low (you may adjust the provided severity_hint if the raw \
note itself suggests otherwise, but default to the hint)
- confidence: how confident you are in this finding given the raw note
- recommended_question: a specific, actionable follow-up question to put to management

Return one item per finding_id provided, preserving the finding_id exactly so it can be \
matched back to its source. Do not invent findings not present in the input."""


def _build_user_prompt(findings: list[RawFinding]) -> str:
    payload = [f.model_dump(mode="json") for f in findings]
    return "Raw findings:\n" + json.dumps(payload, indent=2)


def _collect_financial_findings(
    financial_summary: FinancialSummary | None,
) -> tuple[list[RawFinding], dict[str, SourceReference]]:
    findings: list[RawFinding] = []
    sources: dict[str, SourceReference] = {}
    if not financial_summary:
        return findings, sources

    for n, anomaly in enumerate(financial_summary.anomalies, start=1):
        finding_id = f"FIN-{n}"
        severity = _coerce_severity(anomaly.severity)
        findings.append(
            RawFinding(
                finding_id=finding_id,
                category=DDCategory.FINANCIAL,
                module="financial",
                severity_hint=severity,
                raw_note=anomaly.description,
            )
        )
        sources[finding_id] = SourceReference(
            file=anomaly.source_file or "(financial summary — source file not recorded)",
            excerpt=anomaly.description,
        )
    return findings, sources


def _collect_contract_findings(
    contracts_register: ContractsRegister | None,
) -> tuple[list[RawFinding], dict[str, SourceReference]]:
    findings: list[RawFinding] = []
    sources: dict[str, SourceReference] = {}
    if not contracts_register:
        return findings, sources

    n = 0
    for entry in contracts_register.entries:
        for flag in entry.flags:
            n += 1
            finding_id = f"LEG-{n}"
            severity = _coerce_severity(flag.severity)
            counterparty = entry.counterparty or "counterparty not identified"
            findings.append(
                RawFinding(
                    finding_id=finding_id,
                    category=DDCategory.CORPORATE_LEGAL,
                    module="legal",
                    severity_hint=severity,
                    raw_note=f"[{entry.file_path}, counterparty: {counterparty}] {flag.description}",
                )
            )
            sources[finding_id] = SourceReference(file=entry.file_path, excerpt=flag.description)
    return findings, sources


def _collect_coverage_gap_findings(
    index: DocumentIndex,
) -> tuple[list[RawFinding], dict[str, SourceReference]]:
    findings: list[RawFinding] = []
    sources: dict[str, SourceReference] = {}

    present = {e.category for e in index.entries if e.category != DDCategory.UNCLASSIFIED}
    missing = [c for c in DDCategory if c not in present and c != DDCategory.UNCLASSIFIED]

    for n, category in enumerate(missing, start=1):
        finding_id = f"GAP-{n}"
        findings.append(
            RawFinding(
                finding_id=finding_id,
                category=category,
                module="classification",
                severity_hint=Severity.LOW,
                raw_note=(
                    f"No documents classified as '{category.value}' were identified among the "
                    f"{len(index.entries)} documents indexed from the data room."
                ),
            )
        )
        sources[finding_id] = SourceReference(
            file=index.dataroom_path,
            excerpt=f"Document index contains no entries classified as '{category.value}'.",
        )
    return findings, sources


def build_risk_register(
    index: DocumentIndex,
    financial_summary: FinancialSummary | None,
    contracts_register: ContractsRegister | None,
    model_config: ModelConfig,
) -> RiskRegister:
    fin_findings, fin_sources = _collect_financial_findings(financial_summary)
    legal_findings, legal_sources = _collect_contract_findings(contracts_register)
    gap_findings, gap_sources = _collect_coverage_gap_findings(index)

    findings = fin_findings + legal_findings + gap_findings
    sources = {**fin_sources, **legal_sources, **gap_sources}

    logger.info(
        "Collected %d raw finding(s): %d financial, %d legal, %d coverage gap",
        len(findings), len(fin_findings), len(legal_findings), len(gap_findings),
    )

    if not findings:
        return RiskRegister(run_id=index.run_id, items=[])

    drafts_by_id = {}
    try:
        batch = structured_call(
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=_build_user_prompt(findings),
            output_model=RiskDraftBatch,
            model_config=model_config,
        )
        drafts_by_id = {d.finding_id: d for d in batch.items}
    except StructuredCallError as exc:
        logger.error(
            "Risk phrasing call failed: %s — falling back to raw finding text for all items", exc
        )

    items: list[RiskRegisterItem] = []
    for finding in findings:
        draft = drafts_by_id.get(finding.finding_id)
        source = sources[finding.finding_id]

        if draft is None:
            title = finding.raw_note[:80]
            description = finding.raw_note
            severity = finding.severity_hint
            confidence = Confidence.LOW
            recommended_question = "Please ask management to clarify and provide supporting documentation."
        else:
            title = draft.title
            description = draft.description
            severity = draft.severity
            confidence = draft.confidence
            recommended_question = draft.recommended_question

        items.append(
            RiskRegisterItem(
                id="PENDING",  # reassigned below, after severity sort
                category=finding.category,
                title=title,
                description=description,
                severity=severity,
                confidence=confidence,
                module=finding.module,
                source_documents=[source],
                recommended_question=recommended_question,
            )
        )

    items.sort(key=lambda item: _SEVERITY_RANK[item.severity])
    for n, item in enumerate(items, start=1):
        item.id = f"RISK-{n:04d}"

    return RiskRegister(run_id=index.run_id, items=items)
