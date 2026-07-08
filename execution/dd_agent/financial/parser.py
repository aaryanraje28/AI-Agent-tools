"""Financial statement parsing (P&L, balance sheet, cash flow) from documents already
classified as category="Financial" in the document index.

Reads each Financial-classified document's cached text and asks Claude to extract
period-labeled line items (schema: dd_agent.schemas.financial.FinancialExtractionResult).
`source_file` on each resulting PeriodFigure is attached by this code, not by the model,
so provenance is guaranteed rather than self-reported.
"""
from __future__ import annotations

import logging
from pathlib import Path

from dd_agent.config import ModelConfig
from dd_agent.llm.claude_client import StructuredCallError, structured_call
from dd_agent.schemas.common import DDCategory, DocumentStatus
from dd_agent.schemas.document_index import DocumentIndex
from dd_agent.schemas.financial import FinancialExtractionResult, FinancialSummary, PeriodFigure

logger = logging.getLogger("dd_agent.financial.parser")

_MAX_CHARS = 12_000

_SYSTEM_PROMPT = """You are a financial due diligence analyst extracting period-labeled \
financial figures from a document sourced from an M&A data room. Extract only figures \
that are explicitly stated or directly computable from the text — never estimate or \
fabricate a number that isn't supported by the document. Leave a field null if it is not \
present. Identify one-off / non-recurring items mentioned (e.g. litigation settlements, \
asset write-offs, restructuring costs) as `ebitda_adjustments` and, where the document \
allows you to compute it, an EBITDA figure with those items normalized out as \
`ebitda_adjusted`. If the document contains no period-labeled financial figures at all, \
return an empty periods list."""


def _build_user_prompt(file_path: str, text: str) -> str:
    truncated = text[:_MAX_CHARS]
    suffix = "\n\n[...truncated...]" if len(text) > _MAX_CHARS else ""
    return f"Document filename: {file_path}\n\nDocument text:\n{truncated}{suffix}"


def parse_financial_statements(index: DocumentIndex, model_config: ModelConfig) -> FinancialSummary:
    financial_docs = [
        e for e in index.entries
        if e.category == DDCategory.FINANCIAL
        and e.status in (DocumentStatus.OK, DocumentStatus.OCR_LOW_CONFIDENCE)
        and e.text_cache_path
    ]
    logger.info("Parsing financial statements from %d document(s)", len(financial_docs))

    periods: list[PeriodFigure] = []
    for entry in financial_docs:
        try:
            text = Path(entry.text_cache_path).read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("%s: could not read text cache: %s", entry.file_path, exc)
            continue
        if not text.strip():
            continue

        try:
            result = structured_call(
                system_prompt=_SYSTEM_PROMPT,
                user_prompt=_build_user_prompt(entry.file_path, text),
                output_model=FinancialExtractionResult,
                model_config=model_config,
            )
        except StructuredCallError as exc:
            logger.error("Financial extraction failed for %s: %s", entry.file_path, exc)
            continue

        for extracted in result.periods:
            periods.append(PeriodFigure(**extracted.model_dump(), source_file=entry.file_path))

    return FinancialSummary(run_id=index.run_id, periods=periods)
