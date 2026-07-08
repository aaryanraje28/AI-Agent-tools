#!/usr/bin/env python
"""CLI entry point for the Indian Family Office Sourcing pipeline.

Usage (at least one of --tracxn-raw / --sebi-raw / --web-signals is required):
    python family_office_sourcing.py --web-signals .tmp/<run_id>/web_signals.json \
        [--tracxn-raw .tmp/<run_id>/tracxn_raw.json] [--sebi-raw .tmp/<run_id>/sebi_aif.json] \
        --output .tmp/<run_id>/ [--skip-enrichment] [--skip-sheets] [--sheet-id <id>] \
        [--format docx|csv|both]

--web-signals (search-engine-indexed discovery, including LinkedIn via site:linkedin.com
queries) is the primary source; --tracxn-raw is an optional structured cross-check when
available. See directives/family_office_sourcing.md for the source-role changelog.

Raw source JSON is fetched live by the orchestrating agent (Tracxn MCP, SEBI AIF via
WebFetch, web/LinkedIn signals via WebSearch) — see directives/family_office_sourcing.md
for why that step can't live in this script. Everything from here on is deterministic:
ingest -> dedupe -> enrich (unconfirmed candidates only) -> score -> output.
"""
from __future__ import annotations

import argparse
import csv
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
EXECUTION_DIR = REPO_ROOT / "execution"
if str(EXECUTION_DIR) not in sys.path:
    sys.path.insert(0, str(EXECUTION_DIR))

from dotenv import load_dotenv  # noqa: E402

from dd_agent.config import load_settings  # noqa: E402
from family_office_sourcing.config import load_sourcing_config  # noqa: E402
from family_office_sourcing.enrichment import enrich_unconfirmed  # noqa: E402
from family_office_sourcing.ingest import (  # noqa: E402
    normalize_sebi_aif,
    normalize_tracxn,
    normalize_web_signals,
)
from family_office_sourcing.matching import dedupe_candidates  # noqa: E402
from family_office_sourcing.output_docx import render_docx_report, render_docx_report_by_state  # noqa: E402
from family_office_sourcing.output_sheets import _BASE_HEADERS, _row_for, SheetsNotConfigured  # noqa: E402
from family_office_sourcing.schemas import InvestmentMode, SourcingRun  # noqa: E402
from family_office_sourcing.scoring import score_candidates  # noqa: E402

logger = logging.getLogger("family_office_sourcing.cli")


def _make_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _print_summary(run: SourcingRun) -> None:
    by_tier: dict[str, int] = {}
    by_confidence: dict[str, int] = {}
    for record in run.records:
        by_tier[record.priority_tier.value] = by_tier.get(record.priority_tier.value, 0) + 1
        by_confidence[record.confidence.value] = by_confidence.get(record.confidence.value, 0) + 1

    excluded = sum(1 for r in run.records if r.exclusion_reason)
    by_mode: dict[str, int] = {}
    for record in run.records:
        by_mode[record.investment_mode.value] = by_mode.get(record.investment_mode.value, 0) + 1

    print(f"\nRun {run.run_id}: {len(run.records)} candidates after dedupe")
    print("By tier: " + ", ".join(f"{t}: {by_tier.get(t, 0)}" for t in ("A", "B", "C")))
    print("By confidence: " + ", ".join(f"{k}: {v}" for k, v in by_confidence.items()))
    print("By investment mode: " + ", ".join(f"{k}: {v}" for k, v in by_mode.items()))
    if excluded:
        print(f"Excluded (non-family-office pattern match, unconfirmed only): {excluded}")


def _write_csv(run: SourcingRun, path: Path, group_by: str = "tier") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if group_by == "state":
        sort_key = lambda r: (r.state or "zzz_no_state", r.priority_tier.value, -r.priority_score)  # noqa: E731
    else:
        sort_key = lambda r: (r.priority_tier.value, -r.priority_score)  # noqa: E731
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(_BASE_HEADERS)
        for record in sorted(run.records, key=sort_key):
            writer.writerow(_row_for(record))
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Indian Family Office Sourcing pipeline")
    parser.add_argument("--tracxn-raw", default=None, help="Path to raw Tracxn investor-search JSON (optional structured cross-check)")
    parser.add_argument("--sebi-raw", default=None, help="Path to raw SEBI AIF registry JSON")
    parser.add_argument("--web-signals", default=None, help="Path to raw web/LinkedIn search-hit JSON (primary discovery source)")
    parser.add_argument("--output", required=True, help="Directory to write output artifacts")
    parser.add_argument("--config", default=None, help="Path to config.yaml (default: repo root)")
    parser.add_argument("--skip-enrichment", action="store_true", help="Skip Claude plausibility assessment of unconfirmed candidates")
    parser.add_argument("--skip-sheets", action="store_true", help="Skip Google Sheet sync even if --sheet-id is given")
    parser.add_argument("--sheet-id", default=None, help="Google Sheet spreadsheet ID to sync into")
    parser.add_argument("--format", choices=["docx", "csv", "both"], default="both")
    parser.add_argument("--city", default=None, help="Filter output to a single city (case-insensitive, e.g. Mumbai). Produces city-suffixed output filenames.")
    parser.add_argument("--state", default=None, help="Filter output to a single state (case-insensitive, e.g. Maharashtra). Combinable with --city; produces state-suffixed output filenames.")
    parser.add_argument("--group-by", choices=["tier", "state"], default="tier", help="docx report section grouping: 'tier' (default, A/B/C) or 'state' (one section per Indian state, for an all-India state-by-state cut instead of filtering to one)")
    parser.add_argument("--investment-mode", choices=["lp", "direct"], default=None, help="Filter to LP/Allocator-style or Direct/Co-Investor-style candidates only, per deterministic keyword classification (most candidates classify as Unknown and are excluded by either choice — see directive).")
    parser.add_argument("--exclude-flagged", action="store_true", help="Drop candidates matching the non-family-office exclusion filter (VC firm/bank/wealth manager/advisor patterns) entirely instead of just pinning them to Tier C.")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    if not (args.tracxn_raw or args.sebi_raw or args.web_signals):
        parser.error("at least one of --tracxn-raw, --sebi-raw, --web-signals is required")

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )

    load_dotenv(REPO_ROOT / ".env")
    settings = load_settings(args.config)
    sourcing_config = load_sourcing_config(args.config)

    output_dir = Path(args.output).resolve()
    run_id = _make_run_id()

    candidates = []
    if args.tracxn_raw:
        candidates += normalize_tracxn(Path(args.tracxn_raw))
    if args.sebi_raw:
        candidates += normalize_sebi_aif(Path(args.sebi_raw))
    if args.web_signals:
        candidates += normalize_web_signals(Path(args.web_signals))
    num_sources = bool(args.tracxn_raw) + bool(args.sebi_raw) + bool(args.web_signals)
    logger.info("Ingested %d raw candidates from %d source(s)", len(candidates), num_sources)

    records = dedupe_candidates(candidates, sourcing_config.dedupe_match_threshold)
    logger.info("Deduped to %d candidate records", len(records))

    if args.skip_enrichment:
        logger.info("Skipping enrichment (--skip-enrichment)")
    else:
        records = enrich_unconfirmed(records, settings.model)

    records = score_candidates(records, sourcing_config)

    if args.exclude_flagged:
        before = len(records)
        records = [r for r in records if not r.exclusion_reason]
        logger.info("Dropped %d excluded (non-family-office pattern) candidate(s)", before - len(records))

    if args.investment_mode:
        mode = InvestmentMode.LP_ALLOCATOR if args.investment_mode == "lp" else InvestmentMode.DIRECT_COINVESTOR
        before = len(records)
        records = [r for r in records if r.investment_mode == mode]
        if not records:
            parser.error(f"--investment-mode {args.investment_mode!r} matched none of the {before} candidates in this run")
        logger.info("Filtered to %d candidate(s) with investment_mode=%s", len(records), mode.value)

    suffix_parts = []
    if args.city:
        before = len(records)
        records = [r for r in records if r.city and r.city.strip().lower() == args.city.strip().lower()]
        if not records:
            parser.error(f"--city {args.city!r} matched none of the {before} candidates in this run")
        suffix_parts.append(args.city.strip())
        logger.info("Filtered to %d candidate(s) in city=%s", len(records), args.city)
    if args.state:
        before = len(records)
        records = [r for r in records if r.state and r.state.strip().lower() == args.state.strip().lower()]
        if not records:
            parser.error(f"--state {args.state!r} matched none of the {before} candidates in this run")
        suffix_parts.append(args.state.strip())
        logger.info("Filtered to %d candidate(s) in state=%s", len(records), args.state)
    suffix = "_" + re.sub(r"[^A-Za-z0-9]+", "_", "_".join(suffix_parts)).strip("_") if suffix_parts else ""

    run = SourcingRun(run_id=run_id, records=records)

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / f"candidates{suffix}.json").write_text(run.model_dump_json(indent=2), encoding="utf-8")
    logger.info("Wrote %s", output_dir / f"candidates{suffix}.json")

    _print_summary(run)

    if args.format in ("docx", "both"):
        docx_name = sourcing_config.output.docx_filename
        if args.group_by == "state":
            docx_name = docx_name.replace(".docx", "_by_state.docx")
            path = render_docx_report_by_state(run, settings.report, output_dir / docx_name)
        else:
            if suffix:
                docx_name = docx_name.replace(".docx", f"{suffix}.docx")
            path = render_docx_report(run, settings.report, output_dir / docx_name)
        print(f"\nWrote {path}")
    if args.format in ("csv", "both"):
        csv_suffix = "_by_state" if args.group_by == "state" else suffix
        path = _write_csv(run, output_dir / f"candidates{csv_suffix}.csv", group_by=args.group_by)
        print(f"Wrote {path}")

    if args.skip_sheets:
        logger.info("Skipping Google Sheet sync (--skip-sheets)")
    elif not args.sheet_id:
        logger.info("No --sheet-id given, skipping Google Sheet sync")
    else:
        try:
            from family_office_sourcing.output_sheets import sync_to_sheet

            sync_to_sheet(
                run, args.sheet_id, sourcing_config.output.sheet_name,
                sourcing_config.output.preserve_columns, REPO_ROOT,
            )
            print(f"Synced to Google Sheet {args.sheet_id}")
        except SheetsNotConfigured as exc:
            logger.warning("Google Sheet sync skipped: %s", exc)
        except ImportError as exc:
            logger.warning("Google Sheet sync skipped, dependency not installed: %s", exc)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
