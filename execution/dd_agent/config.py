"""Loads config.yaml from the repo root into a validated settings object."""
from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = REPO_ROOT / "config.yaml"


class ModelConfig(BaseModel):
    name: str = "claude-sonnet-5"
    max_tokens: int = 4096
    temperature: float = 0


class MaterialityConfig(BaseModel):
    revenue_variance_pct: float = 15
    ebitda_adjustment_pct: float = 10
    related_party_revenue_pct: float = 5
    contract_value_threshold: float = 5_000_000


class TaxonomyConfig(BaseModel):
    categories: list[str]


class IngestionConfig(BaseModel):
    supported_extensions: list[str]
    ocr_enabled: bool = True
    ocr_min_confidence: int = 60
    max_file_size_mb: int = 200


class ReportPalette(BaseModel):
    navy: str
    gold: str
    cream: str


class ReportTableStyle(BaseModel):
    header_fill: str = "navy"
    header_text: str = "cream"
    body_text: str = "1F2A44"
    border: str = "thin"


class ReportConfig(BaseModel):
    heading_font: str = "Cambria"
    body_font: str = "Cambria"
    palette: ReportPalette
    table: ReportTableStyle = ReportTableStyle()
    word_template_compatibility: bool = True


class Settings(BaseModel):
    model: ModelConfig
    materiality: MaterialityConfig
    taxonomy: TaxonomyConfig
    ingestion: IngestionConfig
    report: ReportConfig


def load_settings(config_path: Path | str | None = None) -> Settings:
    path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return Settings(**raw)
