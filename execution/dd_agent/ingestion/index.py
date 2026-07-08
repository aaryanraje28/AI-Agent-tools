"""Builds and persists the document index: scan -> extract -> cache text -> index entry.

Classification (category, entities, summary) is deliberately NOT done here — see
execution/dd_agent/classification/classifier.py, which enriches entries produced by
`build_document_index` in a separate pass. Keeping extraction and classification apart
means either can be swapped independently (per the modular requirement in the directive).
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

from dd_agent.config import IngestionConfig
from dd_agent.ingestion.extractors import extract
from dd_agent.ingestion.scanner import scan_dataroom
from dd_agent.schemas.document_index import DocumentIndex, DocumentIndexEntry

logger = logging.getLogger("dd_agent.ingestion.index")


def _doc_id(relative_path: str) -> str:
    return hashlib.sha1(relative_path.encode("utf-8")).hexdigest()[:12]


def build_document_index(
    dataroom_root: Path,
    run_id: str,
    tmp_dir: Path,
    ingestion_config: IngestionConfig,
) -> DocumentIndex:
    """Scan the data room, extract text for every eligible file, and build the index.

    Extracted text is cached under `tmp_dir/text/<doc_id>.txt` so later modules
    (classification, financial, legal) can re-read it without re-parsing source files.
    """
    text_cache_dir = tmp_dir / "text"
    text_cache_dir.mkdir(parents=True, exist_ok=True)

    scanned_files = scan_dataroom(dataroom_root, ingestion_config)
    entries: list[DocumentIndexEntry] = []

    for scanned in scanned_files:
        doc_id = _doc_id(scanned.relative_path)
        logger.info("Extracting %s (%s)", scanned.relative_path, scanned.file_type)

        result = extract(
            scanned.absolute_path,
            file_type=scanned.file_type,
            ocr_enabled=ingestion_config.ocr_enabled,
            ocr_min_confidence=ingestion_config.ocr_min_confidence,
        )

        text_cache_path: str | None = None
        if result.text.strip():
            cache_file = text_cache_dir / f"{doc_id}.txt"
            cache_file.write_text(result.text, encoding="utf-8")
            text_cache_path = str(cache_file)

        if result.error:
            logger.warning("Extraction issue for %s: %s", scanned.relative_path, result.error)

        entries.append(
            DocumentIndexEntry(
                doc_id=doc_id,
                file_path=scanned.relative_path,
                file_type=scanned.file_type,
                status=result.status,
                page_count=result.page_count,
                text_cache_path=text_cache_path,
                ocr_used=result.ocr_used,
                error=result.error,
            )
        )

    index = DocumentIndex(run_id=run_id, dataroom_path=str(dataroom_root), entries=entries)
    logger.info("Built document index: %d entries", len(entries))
    return index


def save_document_index(index: DocumentIndex, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / "document_index.json"
    out_path.write_text(index.model_dump_json(indent=2, exclude_none=False), encoding="utf-8")
    logger.info("Wrote document index to %s", out_path)
    return out_path


def load_document_index(path: Path) -> DocumentIndex:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return DocumentIndex.model_validate(data)
