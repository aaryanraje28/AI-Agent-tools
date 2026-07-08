"""Contract term extraction + contracts register.

Reads each Corporate/Legal-classified document's cached text and asks Claude whether it's
an actual contract and, if so, to extract its key terms (schema:
dd_agent.schemas.contracts.ContractExtraction). Documents the model marks as not a
contract (board resolutions, certificates, policies, etc.) are excluded from the register.

`contract_id` / `file_path` are attached by this code from the document index entry, not
reported by the model. Missing-signature and expired-term flags are computed
deterministically in code (not left to the model to notice), and merged with any
in-text flags (e.g. unusual liability terms) the model identifies.
"""
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

from dd_agent.config import ModelConfig
from dd_agent.llm.claude_client import StructuredCallError, structured_call
from dd_agent.schemas.common import DDCategory, DocumentStatus
from dd_agent.schemas.contracts import ContractExtraction, ContractFlag, ContractRegisterEntry, ContractsRegister
from dd_agent.schemas.document_index import DocumentIndex

logger = logging.getLogger("dd_agent.legal.contract_review")

_MAX_CHARS = 12_000

_SYSTEM_PROMPT = """You are a due diligence analyst reviewing a document from an M&A data \
room's Corporate/Legal folder. First determine whether it is an actual contract \
(a bilateral or multilateral agreement — MSA, lease, employment agreement, license, NDA, \
etc.) as opposed to a board resolution, certificate, policy, or other non-contract legal \
document. If it is a contract, extract: counterparty, contract type, effective date, term \
end date, renewal terms, termination clause, change-of-control clause, exclusivity \
clause, indemnification cap, governing law, contract value, and whether signature blocks \
appear filled/executed. Flag any unusual liability terms, one-sided indemnification, or \
other issues visible directly in the text — do not flag missing signatures or expiry, \
those are checked separately. Extract only what the text supports; leave fields null if \
not stated. Never render a legal opinion or conclusion — describe what the clause says, \
not whether it is enforceable or advisable."""


def _build_user_prompt(file_path: str, text: str) -> str:
    truncated = text[:_MAX_CHARS]
    suffix = "\n\n[...truncated...]" if len(text) > _MAX_CHARS else ""
    return f"Document filename: {file_path}\n\nDocument text:\n{truncated}{suffix}"


def build_contracts_register(index: DocumentIndex, model_config: ModelConfig) -> ContractsRegister:
    candidates = [
        e for e in index.entries
        if e.category == DDCategory.CORPORATE_LEGAL
        and e.status in (DocumentStatus.OK, DocumentStatus.OCR_LOW_CONFIDENCE)
        and e.text_cache_path
    ]
    logger.info("Reviewing %d Corporate/Legal document(s) for contract terms", len(candidates))

    entries: list[ContractRegisterEntry] = []
    today = date.today()

    for doc in candidates:
        try:
            text = Path(doc.text_cache_path).read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("%s: could not read text cache: %s", doc.file_path, exc)
            continue
        if not text.strip():
            continue

        try:
            result = structured_call(
                system_prompt=_SYSTEM_PROMPT,
                user_prompt=_build_user_prompt(doc.file_path, text),
                output_model=ContractExtraction,
                model_config=model_config,
            )
        except StructuredCallError as exc:
            logger.error("Contract extraction failed for %s: %s", doc.file_path, exc)
            continue

        if not result.is_contract:
            logger.debug("%s classified as not a contract; excluding from register", doc.file_path)
            continue

        flags = list(result.flags)
        if result.signed is False:
            flags.append(ContractFlag(description="Signature block appears blank/unexecuted", severity="High"))
        if result.term_end_date is not None and result.term_end_date < today:
            flags.append(
                ContractFlag(
                    description=f"Agreement term ended {result.term_end_date.isoformat()} — appears expired",
                    severity="Medium",
                )
            )

        entries.append(
            ContractRegisterEntry(
                contract_id=doc.doc_id,
                file_path=doc.file_path,
                counterparty=result.counterparty,
                contract_type=result.contract_type,
                effective_date=result.effective_date,
                term_end_date=result.term_end_date,
                renewal_terms=result.renewal_terms,
                termination_clause=result.termination_clause,
                change_of_control_clause=result.change_of_control_clause,
                exclusivity_clause=result.exclusivity_clause,
                indemnification_cap=result.indemnification_cap,
                governing_law=result.governing_law,
                contract_value=result.contract_value,
                signed=result.signed,
                flags=flags,
                extraction_confidence=result.extraction_confidence,
            )
        )

    return ContractsRegister(run_id=index.run_id, entries=entries)
