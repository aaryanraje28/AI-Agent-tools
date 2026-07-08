"""Claude-based classification of each indexed document into a DD category.

Reads the cached extracted text for every OK / OCR_LOW_CONFIDENCE entry in the document
index, asks Claude to classify + extract key entities + summarize (schema-validated JSON
via dd_agent.llm.claude_client), and writes the result back onto the entry. Locked and
unreadable documents are left unclassified — there's nothing to classify.
"""
from __future__ import annotations

import logging
from pathlib import Path

from dd_agent.config import ModelConfig
from dd_agent.llm.claude_client import StructuredCallError, structured_call
from dd_agent.schemas.classification import ClassificationResult
from dd_agent.schemas.common import DocumentStatus
from dd_agent.schemas.document_index import DocumentIndex

logger = logging.getLogger("dd_agent.classification.classifier")

_MAX_CHARS = 12_000  # keep prompts bounded; deep per-clause extraction happens in later modules

_SYSTEM_PROMPT = """You are a due diligence analyst assistant classifying documents from an \
M&A data room. For the document text provided, classify it into exactly one of these \
categories: Corporate/Legal, Financial, Tax, Commercial/Operations, HR, IP, Litigation, \
Compliance/Regulatory, Real Estate/Assets. If none clearly fit, you may still pick the \
closest category but set confidence to Low.

Extract key named entities mentioned (companies, people, dates, monetary amounts, \
jurisdictions) and write a factual 1-2 sentence summary of what the document is and \
contains. Do not editorialize or speculate beyond what the text supports."""


def _build_user_prompt(file_path: str, text: str) -> str:
    truncated = text[:_MAX_CHARS]
    suffix = "\n\n[...truncated...]" if len(text) > _MAX_CHARS else ""
    return f"Document filename: {file_path}\n\nDocument text:\n{truncated}{suffix}"


def classify_document_index(index: DocumentIndex, model_config: ModelConfig) -> DocumentIndex:
    """Enriches each eligible entry in-place with category/confidence/entities/summary."""
    classifiable = [
        e for e in index.entries
        if e.status in (DocumentStatus.OK, DocumentStatus.OCR_LOW_CONFIDENCE) and e.text_cache_path
    ]
    logger.info("Classifying %d/%d documents", len(classifiable), len(index.entries))

    for entry in classifiable:
        try:
            text = Path(entry.text_cache_path).read_text(encoding="utf-8")
        except OSError as exc:
            entry.error = f"Could not read text cache: {exc}"
            logger.warning("%s: %s", entry.file_path, entry.error)
            continue

        if not text.strip():
            continue

        try:
            result = structured_call(
                system_prompt=_SYSTEM_PROMPT,
                user_prompt=_build_user_prompt(entry.file_path, text),
                output_model=ClassificationResult,
                model_config=model_config,
            )
        except StructuredCallError as exc:
            entry.error = f"Classification failed: {exc}"
            logger.error("%s: %s", entry.file_path, entry.error)
            continue

        entry.category = result.category
        entry.classification_confidence = result.confidence.value
        entry.key_entities = result.key_entities
        entry.summary = result.summary

    return index
