"""Loads the `family_office_sourcing:` section of config.yaml (repo root)."""
from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = REPO_ROOT / "config.yaml"


class ScoringConfig(BaseModel):
    confirmed_source_weight: int = 40
    enrichment_confirmed_weight: int = 35
    sector_fit_weight: int = 25
    tier1_city_weight: int = 15
    recent_activity_weight: int = 20
    tier_a_min_score: int = 70
    tier_b_min_score: int = 40


class OutputConfig(BaseModel):
    docx_filename: str = "Family_Office_Prospects.docx"
    sheet_name: str = "Indian Family Office Prospects"
    preserve_columns: list[str] = ["Status", "Notes"]


class FamilyOfficeSourcingConfig(BaseModel):
    dedupe_match_threshold: int = 88
    scoring: ScoringConfig = ScoringConfig()
    target_sectors: list[str] = []
    output: OutputConfig = OutputConfig()
    # Applied only to unconfirmed (web/LinkedIn) candidates — Tracxn's Single Family Offices
    # feed is already curated and must not be re-filtered by its own description text (a
    # legitimate Tracxn-confirmed SFO can read "Venture capital fund backed by single family
    # office" — excluding on that phrase would wrongly drop it; see directive learnings).
    exclude_office_type_keywords: list[str] = []
    lp_allocator_keywords: list[str] = []
    direct_investor_keywords: list[str] = []


TIER1_CITIES = {"mumbai", "delhi", "new delhi", "gurugram", "gurgaon", "noida", "bengaluru", "bangalore", "chennai", "pune", "hyderabad"}


def load_sourcing_config(config_path: Path | str | None = None) -> FamilyOfficeSourcingConfig:
    path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    section = raw.get("family_office_sourcing", {})
    return FamilyOfficeSourcingConfig(**section)
