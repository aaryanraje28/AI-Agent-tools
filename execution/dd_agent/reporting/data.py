"""Shared, pure data-prep for the report renderers.

Both markdown_report.py and docx_report.py render the same ReportContext so the two
output formats never drift — this module has no LLM calls and no formatting decisions
(font/color/layout), only assembly of the 4 JSON artifacts into one structure.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from dd_agent.financial.analysis import sort_periods
from dd_agent.schemas.common import DDCategory, DocumentStatus, Severity
from dd_agent.schemas.contracts import ContractsRegister
from dd_agent.schemas.document_index import DocumentIndex, DocumentIndexEntry
from dd_agent.schemas.financial import FinancialSummary, PeriodFigure
from dd_agent.schemas.risk_register import RiskRegister, RiskRegisterItem

_SEVERITY_ORDER = {Severity.HIGH: 0, Severity.MEDIUM: 1, Severity.LOW: 2}


@dataclass
class ExecSummaryStats:
    total_documents: int
    documents_by_category: dict[str, int]
    risk_counts_by_severity: dict[str, int]
    contracts_reviewed: int
    periods_covered: list[str]
    needs_attention_count: int  # locked/unreadable documents


@dataclass
class OpenQuestion:
    question: str
    source_file: str
    category: str


@dataclass
class ReportContext:
    run_id: str
    dataroom_path: str
    exec_summary: ExecSummaryStats
    corporate_docs: list[DocumentIndexEntry]
    financial_periods: list[PeriodFigure]
    risk_items: list[RiskRegisterItem]
    contracts: list
    open_questions: list[OpenQuestion]
    document_index_entries: list[DocumentIndexEntry]
    category_gaps: list[str] = field(default_factory=list)


def build_report_context(
    index: DocumentIndex,
    risk_register: RiskRegister,
    contracts_register: ContractsRegister,
    financial_summary: FinancialSummary,
) -> ReportContext:
    documents_by_category: dict[str, int] = {}
    for entry in index.entries:
        documents_by_category[entry.category.value] = documents_by_category.get(entry.category.value, 0) + 1

    risk_counts_by_severity = {"High": 0, "Medium": 0, "Low": 0}
    for item in risk_register.items:
        risk_counts_by_severity[item.severity.value] = risk_counts_by_severity.get(item.severity.value, 0) + 1

    needs_attention = [
        e for e in index.entries if e.status in (DocumentStatus.LOCKED, DocumentStatus.UNREADABLE)
    ]

    exec_summary = ExecSummaryStats(
        total_documents=len(index.entries),
        documents_by_category=documents_by_category,
        risk_counts_by_severity=risk_counts_by_severity,
        contracts_reviewed=len(contracts_register.entries),
        periods_covered=[p.period_label for p in sort_periods(financial_summary.periods)],
        needs_attention_count=len(needs_attention),
    )

    corporate_docs = [e for e in index.entries if e.category == DDCategory.CORPORATE_LEGAL]

    risk_items = sorted(risk_register.items, key=lambda it: _SEVERITY_ORDER[it.severity])

    open_questions: list[OpenQuestion] = []
    seen_questions: set[str] = set()
    for item in risk_items:
        if item.recommended_question in seen_questions:
            continue
        seen_questions.add(item.recommended_question)
        source_file = item.source_documents[0].file if item.source_documents else "N/A"
        open_questions.append(
            OpenQuestion(question=item.recommended_question, source_file=source_file, category=item.category.value)
        )
    for entry in needs_attention:
        question = f"Please provide an accessible copy of '{entry.file_path}' ({entry.status.value}: {entry.error or 'no further detail'})."
        if question not in seen_questions:
            seen_questions.add(question)
            open_questions.append(OpenQuestion(question=question, source_file=entry.file_path, category="Document Access"))

    present_categories = {e.category for e in index.entries if e.category != DDCategory.UNCLASSIFIED}
    category_gaps = [c.value for c in DDCategory if c != DDCategory.UNCLASSIFIED and c not in present_categories]

    return ReportContext(
        run_id=index.run_id,
        dataroom_path=index.dataroom_path,
        exec_summary=exec_summary,
        corporate_docs=corporate_docs,
        financial_periods=sort_periods(financial_summary.periods),
        risk_items=risk_items,
        contracts=contracts_register.entries,
        open_questions=open_questions,
        document_index_entries=index.entries,
        category_gaps=category_gaps,
    )
