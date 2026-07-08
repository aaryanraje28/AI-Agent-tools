"""Fuzzy-dedupes candidates from different sources into one CandidateRecord per real-world
office. Without this step, the same family office tagged by Tracxn and separately surfaced
by a web search would show up as two rows the analyst has to manually notice are the same
entity (see directive edge cases).
"""
from __future__ import annotations

import hashlib
import re

from rapidfuzz import fuzz

from family_office_sourcing.schemas import Candidate, CandidateRecord, Confidence

_NOISE_WORDS = re.compile(
    r"\b(family office|family trust|holdings|investments?|ventures|capital|linkedin|"
    r"pvt\.?|private|limited|ltd\.?|llp|ltd)\b",
    re.IGNORECASE,
)
_NON_ALNUM = re.compile(r"[^a-z0-9 ]")


def _normalize_name(name: str) -> str:
    """Web/LinkedIn search-result titles commonly carry site branding — "Raintree Family
    Office | LinkedIn", "X on LinkedIn" — that isn't part of the entity's name at all. Left
    unstripped, this silently defeated an exact-match merge against Tracxn's "Raintree"
    (caught live: "raintree family office linkedin" scored well below the dedupe threshold
    against "raintree"). "linkedin" is stripped as a noise word alongside the existing
    corporate-suffix words below, same mechanism, same reason."""
    lowered = name.lower()
    stripped = _NOISE_WORDS.sub("", lowered)
    cleaned = _NON_ALNUM.sub(" ", stripped)
    return re.sub(r"\s+", " ", cleaned).strip()


def _candidate_id(display_name: str) -> str:
    return hashlib.sha1(_normalize_name(display_name).encode("utf-8")).hexdigest()[:12]


def _merge_field(candidates: list[Candidate], field: str, prefer_confirmed: bool = True) -> str | None:
    ordered = candidates
    if prefer_confirmed:
        ordered = sorted(candidates, key=lambda c: c.confidence != Confidence.CONFIRMED)
    for c in ordered:
        value = getattr(c, field)
        if value:
            return value
    return None


def _merge_location(candidates: list[Candidate]) -> tuple[str | None, str | None]:
    """City and state must come from the SAME source record, never mixed across sources.

    Picking each field independently (as `_merge_field` does for name/website/principal)
    let two differently-located records merge into a location that doesn't exist — caught
    live: "Kemfin" (Tracxn, state=Maharashtra, no city) merged with "Kemfin Family Office"
    (Tracxn, city=Bengaluru, state=Karnataka) produced "Bengaluru, Maharashtra", a state/city
    pair that isn't real. Whether or not the name-based merge itself was correct, asserting
    a synthesized location that no single source actually stated is exactly what the
    directive's "nothing asserted without a citation" rule exists to prevent."""
    ordered = sorted(candidates, key=lambda c: c.confidence != Confidence.CONFIRMED)
    with_city = next((c for c in ordered if c.city), None)
    if with_city:
        return with_city.city, with_city.state
    with_state = next((c for c in ordered if c.state), None)
    if with_state:
        return None, with_state.state
    return None, None


def dedupe_candidates(candidates: list[Candidate], match_threshold: int = 88) -> list[CandidateRecord]:
    """Greedy single-pass clustering: each candidate joins the first existing cluster whose
    representative name scores >= match_threshold (rapidfuzz token_sort_ratio), else starts
    a new cluster. Confirmed (Tracxn) candidates are processed first so unconfirmed
    web/SEBI signals merge onto an already-established confirmed record where possible,
    rather than confirmed records merging onto a noisier unconfirmed one."""
    ordered = sorted(candidates, key=lambda c: c.confidence != Confidence.CONFIRMED)

    clusters: list[list[Candidate]] = []
    cluster_keys: list[str] = []

    for candidate in ordered:
        key = _normalize_name(candidate.raw_name)
        if not key:
            clusters.append([candidate])
            cluster_keys.append(key)
            continue

        best_idx, best_score = None, 0
        for idx, existing_key in enumerate(cluster_keys):
            if not existing_key:
                continue
            score = fuzz.token_sort_ratio(key, existing_key)
            if score > best_score:
                best_idx, best_score = idx, score

        if best_idx is not None and best_score >= match_threshold:
            clusters[best_idx].append(candidate)
        else:
            clusters.append([candidate])
            cluster_keys.append(key)

    records = []
    for cluster in clusters:
        display_name = _merge_field(cluster, "raw_name") or cluster[0].raw_name
        aliases = sorted({c.raw_name for c in cluster if c.raw_name != display_name})
        confidence = (
            Confidence.CONFIRMED
            if any(c.confidence == Confidence.CONFIRMED for c in cluster)
            else Confidence.UNCONFIRMED
        )
        city, state = _merge_location(cluster)
        records.append(
            CandidateRecord(
                candidate_id=_candidate_id(display_name),
                display_name=display_name,
                aliases=aliases,
                city=city,
                state=state,
                principal=_merge_field(cluster, "principal"),
                website=_merge_field(cluster, "website"),
                confidence=confidence,
                sources=cluster,
            )
        )
    return records
