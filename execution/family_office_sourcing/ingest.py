"""Normalizes raw JSON from each source into `Candidate` objects.

The orchestrator (Claude) fetches raw data live — Tracxn via MCP, SEBI AIF via WebFetch,
open-web/LinkedIn signals via WebSearch — and saves it to `.tmp/<run_id>/`. Everything in
this module is deterministic: no network calls, no LLM calls, just reshaping.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from family_office_sourcing.schemas import Candidate, Confidence, SourceType

_PRINCIPAL_PATTERN = re.compile(
    r"(?:family office|office) of\s+(?:the\s+)?([A-Z][A-Za-z.’' -]+?)(?:,| focused| investing| based|$)"
)


def _extract_principal(description: str | None) -> str | None:
    """Pulls a named principal out of Tracxn-style descriptions like "Single family office
    of Karan Thapar, chairman of Greaves" — deterministic text extraction only, never a
    guess. Returns None rather than a low-confidence partial match.

    Anchored specifically on "(family) office of <Name>" rather than a bare "of <Name>,"
    — a broader pattern originally also fired on unrelated text like "Provider of BPO
    services for healthcare, logistics..." (the capitalized acronym "BPO" looked enough
    like a name to match), misattributing a made-up "principal" to a record whose
    description was never about a person. Caught during the first live smoke test against
    real Tracxn data — see directive learnings, 2026-07-06."""
    if not description:
        return None
    match = _PRINCIPAL_PATTERN.search(description)
    return match.group(1).strip() if match else None


def _tracxn_name(raw: dict) -> str:
    name = raw.get("name")
    if isinstance(name, dict):
        return " ".join(part for part in (name.get("firstName"), name.get("lastName")) if part).strip()
    return str(name) if name else "(unnamed)"


def _tracxn_location(raw: dict) -> tuple[str | None, str | None]:
    locations = raw.get("locations") or []
    if not locations:
        return None, None
    loc = locations[0]
    city = (loc.get("city") or {}).get("name")
    state = (loc.get("state") or {}).get("name")
    return city, state


def _tracxn_website(raw: dict) -> str | None:
    for entry in raw.get("website") or []:
        if entry.get("url"):
            return entry["url"]
    return None


def normalize_tracxn(raw_path: Path) -> list[Candidate]:
    """`raw_path` holds either one feed block `{"feed": ..., "results": [...]}` or a list of
    such blocks (one per Tracxn feed queried, e.g. Single Family Offices)."""
    data = json.loads(Path(raw_path).read_text(encoding="utf-8"))
    blocks = data if isinstance(data, list) else [data]

    candidates: list[Candidate] = []
    for block in blocks:
        feed_name = block.get("feed", "Tracxn")
        for entry in block.get("results", []):
            description = entry.get("detailedDescription") or entry.get("shortDescription")
            contact = entry.get("contactDetail") or {}
            website = _tracxn_website(entry)
            candidates.append(
                Candidate(
                    source_type=SourceType.TRACXN,
                    source_name=f"Tracxn: {feed_name} feed",
                    source_url=website,
                    raw_name=_tracxn_name(entry),
                    confidence=Confidence.CONFIRMED,
                    city=_tracxn_location(entry)[0],
                    state=_tracxn_location(entry)[1],
                    principal=_extract_principal(description),
                    short_description=description,
                    website=website,
                    has_phone_on_file=(contact.get("hasPhoneNumber") == "YES") if "hasPhoneNumber" in contact else None,
                    has_recent_investment=(entry.get("hasInvestment") == "Yes") if "hasInvestment" in entry else None,
                    external_id=entry.get("id"),
                    evidence_excerpt=description,
                )
            )
    return candidates


def normalize_sebi_aif(raw_path: Path) -> list[Candidate]:
    """`raw_path` holds a list of AIF registry rows: sponsor_name is treated as the
    candidate — an AIF sponsor is frequently the promoter/family's own investment vehicle,
    but this is a signal, not a confirmed family office (see directive edge cases)."""
    rows: list[dict[str, Any]] = json.loads(Path(raw_path).read_text(encoding="utf-8"))
    candidates = []
    for row in rows:
        sponsor = row.get("sponsor_name")
        if not sponsor:
            continue
        aif_name = row.get("aif_name", "")
        category = row.get("category", "")
        candidates.append(
            Candidate(
                source_type=SourceType.SEBI_AIF,
                source_name="SEBI AIF Registry",
                source_url=row.get("source_url"),
                raw_name=sponsor,
                confidence=Confidence.UNCONFIRMED,
                evidence_excerpt=row.get("excerpt") or f"Sponsor of {aif_name} ({category})".strip(),
            )
        )
    return candidates


def normalize_web_signals(raw_path: Path) -> list[Candidate]:
    """`raw_path` holds a list of web/LinkedIn search hits: {query, title, url, snippet}.
    `raw_name` is the page title verbatim — matching.py's fuzzy dedupe, not this module, is
    responsible for reconciling it against a cleaner Tracxn/SEBI name."""
    rows: list[dict[str, Any]] = json.loads(Path(raw_path).read_text(encoding="utf-8"))
    candidates = []
    for row in rows:
        if not row.get("title"):
            continue
        url = row.get("url") or ""
        # Tag LinkedIn-indexed hits distinctly from generic web/news hits so the output's
        # "Source(s)" column stays honest about provenance — this is a search-engine-indexed
        # public result, never an authenticated/scraped LinkedIn page (see directive
        # non-goals: no LinkedIn scraping or login-based access, ToS compliance).
        label = "LinkedIn (via web search)" if "linkedin.com" in url else "Web search"
        candidates.append(
            Candidate(
                source_type=SourceType.WEB_SIGNAL,
                source_name=f"{label}: {row.get('query', '')}".strip(": "),
                source_url=row.get("url"),
                raw_name=row["title"],
                confidence=Confidence.UNCONFIRMED,
                evidence_excerpt=row.get("snippet"),
            )
        )
    return candidates
