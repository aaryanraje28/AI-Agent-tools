"""Recursively scans a data room folder and yields candidate files for ingestion."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from dd_agent.config import IngestionConfig

logger = logging.getLogger("dd_agent.ingestion.scanner")

_TYPE_BY_EXT = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".xlsx": "xlsx",
    ".xls": "xlsx",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".tiff": "image",
}


@dataclass(frozen=True)
class ScannedFile:
    absolute_path: Path
    relative_path: str
    file_type: str
    size_bytes: int


def scan_dataroom(root: Path, ingestion_config: IngestionConfig) -> list[ScannedFile]:
    """Walk `root` recursively and return every supported, size-eligible file.

    Files with unsupported extensions or over the configured size cap are skipped and
    logged rather than raising — one odd file in a data room should never abort a scan.
    """
    if not root.exists() or not root.is_dir():
        raise NotADirectoryError(f"Data room path is not a directory: {root}")

    supported = {ext.lower() for ext in ingestion_config.supported_extensions}
    max_bytes = ingestion_config.max_file_size_mb * 1024 * 1024

    results: list[ScannedFile] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        ext = path.suffix.lower()
        if ext not in supported:
            logger.debug("Skipping unsupported file type: %s", path)
            continue

        size_bytes = path.stat().st_size
        if size_bytes > max_bytes:
            logger.warning(
                "Skipping %s: %.1fMB exceeds max_file_size_mb=%d",
                path, size_bytes / (1024 * 1024), ingestion_config.max_file_size_mb,
            )
            continue

        results.append(
            ScannedFile(
                absolute_path=path,
                relative_path=str(path.relative_to(root)),
                file_type=_TYPE_BY_EXT[ext],
                size_bytes=size_bytes,
            )
        )

    logger.info("Scanned %s: %d eligible files", root, len(results))
    return results
