"""Renders the DD summary report as Markdown from the 4 JSON artifacts.

Pure rendering, no LLM calls — every line traces back to document_index / risk_register /
contracts_register / financial_summary via dd_agent.reporting.data.build_report_context.
A DD category with zero documents still gets a line noting the gap (see Executive
Summary / category_gaps), never silently omitted.
"""
from __future__ import annotations

from pathlib import Path

from dd_agent.reporting.data import ReportContext, build_report_context
from dd_agent.schemas.contracts import ContractsRegister
from dd_agent.schemas.document_index import DocumentIndex
from dd_agent.schemas.financial import FinancialSummary
from dd_agent.schemas.risk_register import RiskRegister


def _executive_summary(ctx: ReportContext) -> str:
    lines = ["## Executive Summary", ""]
    lines.append(f"- **{ctx.exec_summary.total_documents}** documents reviewed from the data room.")
    if ctx.exec_summary.documents_by_category:
        breakdown = ", ".join(
            f"{v} {k}" for k, v in sorted(ctx.exec_summary.documents_by_category.items(), key=lambda kv: -kv[1])
        )
        lines.append(f"- Document mix: {breakdown}.")
    rc = ctx.exec_summary.risk_counts_by_severity
    lines.append(
        f"- **{sum(rc.values())}** risk register items identified: "
        f"{rc.get('High', 0)} High, {rc.get('Medium', 0)} Medium, {rc.get('Low', 0)} Low."
    )
    if ctx.exec_summary.periods_covered:
        lines.append(f"- Financial data covers: {', '.join(ctx.exec_summary.periods_covered)}.")
    else:
        lines.append("- No financial statement data was extracted from the data room.")
    lines.append(f"- **{ctx.exec_summary.contracts_reviewed}** contract(s) reviewed and entered into the contracts register.")
    if ctx.exec_summary.needs_attention_count:
        lines.append(
            f"- **{ctx.exec_summary.needs_attention_count}** document(s) could not be read "
            f"(locked or unreadable) — see Open Questions for Management."
        )
    if ctx.category_gaps:
        lines.append(f"- No documentation was provided for: {', '.join(ctx.category_gaps)}.")
    lines.append("")
    return "\n".join(lines)


def _corporate_structure(ctx: ReportContext) -> str:
    lines = ["## Corporate Structure", ""]
    if not ctx.corporate_docs:
        lines.append("No Corporate/Legal documents were provided in the data room.")
    else:
        lines.append(
            "_v1 does not perform dedicated corporate structure extraction; the Corporate/Legal "
            "documents identified are listed below for analyst review._"
        )
        lines.append("")
        for doc in ctx.corporate_docs:
            lines.append(f"- **{doc.file_path}** — {doc.summary or '(no summary available)'}")
    lines.append("")
    return "\n".join(lines)


def _financial_overview(ctx: ReportContext) -> str:
    lines = ["## Financial Overview", ""]
    if not ctx.financial_periods:
        lines.append("No financial statement data was extracted from the data room.")
        lines.append("")
        return "\n".join(lines)

    headers = ["Line Item"] + [p.period_label for p in ctx.financial_periods]
    rows = {
        "Revenue": [p.revenue for p in ctx.financial_periods],
        "EBITDA (Reported)": [p.ebitda_reported for p in ctx.financial_periods],
        "EBITDA (Adjusted)": [p.ebitda_adjusted for p in ctx.financial_periods],
        "Net Working Capital": [p.net_working_capital for p in ctx.financial_periods],
        "Total Debt": [p.total_debt for p in ctx.financial_periods],
        "Related-Party Revenue": [p.related_party_revenue for p in ctx.financial_periods],
    }
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("|" + "---|" * len(headers))
    for label, values in rows.items():
        formatted = [f"{v:,.0f}" if v is not None else "—" for v in values]
        lines.append(f"| {label} | " + " | ".join(formatted) + " |")
    lines.append("")

    adjustments = sorted({item for p in ctx.financial_periods for item in p.ebitda_adjustments})
    if adjustments:
        lines.append("**EBITDA normalization adjustments noted:** " + "; ".join(adjustments))
        lines.append("")

    sources = sorted({p.source_file for p in ctx.financial_periods if p.source_file})
    if sources:
        lines.append("_Source: " + ", ".join(sources) + "_")
        lines.append("")
    return "\n".join(lines)


def _key_risks(ctx: ReportContext) -> str:
    lines = ["## Key Risks & Red Flags", ""]
    if not ctx.risk_items:
        lines.append("No risk register items were identified.")
        lines.append("")
        return "\n".join(lines)

    for item in ctx.risk_items:
        lines.append(f"### [{item.id}] {item.title} — {item.severity.value}")
        lines.append(f"*Category: {item.category.value} | Confidence: {item.confidence.value}*")
        lines.append("")
        lines.append(item.description)
        lines.append("")
        sources = "; ".join(
            s.file + (f", p.{s.page}" if s.page else "") for s in item.source_documents
        )
        lines.append(f"**Source:** {sources}")
        lines.append(f"**Recommended question for management:** {item.recommended_question}")
        lines.append("")
    return "\n".join(lines)


def _contracts_summary(ctx: ReportContext) -> str:
    lines = ["## Contracts Summary", ""]
    if not ctx.contracts:
        lines.append("No contracts were identified in the Corporate/Legal documents provided.")
        lines.append("")
        return "\n".join(lines)

    headers = ["Counterparty", "Type", "Term End", "Value", "Flags", "Source"]
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("|" + "---|" * len(headers))
    for c in ctx.contracts:
        flags = "; ".join(f.description for f in c.flags) if c.flags else "—"
        term_end = c.term_end_date.isoformat() if c.term_end_date else "—"
        lines.append(
            f"| {c.counterparty or '—'} | {c.contract_type or '—'} | {term_end} | "
            f"{c.contract_value or '—'} | {flags} | {c.file_path} |"
        )
    lines.append("")
    return "\n".join(lines)


def _open_questions(ctx: ReportContext) -> str:
    lines = ["## Open Questions for Management", ""]
    if not ctx.open_questions:
        lines.append("No open questions identified.")
        lines.append("")
        return "\n".join(lines)
    for q in ctx.open_questions:
        lines.append(f"- **[{q.category}]** {q.question} _(source: {q.source_file})_")
    lines.append("")
    return "\n".join(lines)


def _document_index(ctx: ReportContext) -> str:
    lines = ["## Document Index", ""]
    headers = ["File", "Category", "Status", "Pages"]
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("|" + "---|" * len(headers))
    for e in ctx.document_index_entries:
        lines.append(f"| {e.file_path} | {e.category.value} | {e.status.value} | {e.page_count or '—'} |")
    lines.append("")
    return "\n".join(lines)


def render_markdown_report(
    index: DocumentIndex,
    risk_register: RiskRegister,
    contracts_register: ContractsRegister,
    financial_summary: FinancialSummary,
    output_path: Path,
) -> Path:
    ctx = build_report_context(index, risk_register, contracts_register, financial_summary)

    sections = [
        f"# Due Diligence Summary\n\n_Data room: {ctx.dataroom_path}_  \n_Run: {ctx.run_id}_\n",
        _executive_summary(ctx),
        _corporate_structure(ctx),
        _financial_overview(ctx),
        _key_risks(ctx),
        _contracts_summary(ctx),
        _open_questions(ctx),
        _document_index(ctx),
    ]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(sections), encoding="utf-8")
    return output_path
