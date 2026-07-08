#!/usr/bin/env python
"""CLI entry point for the Due Diligence Agent.

Usage:
    python dd_agent.py --input /path/to/dataroom --output /path/to/report

Pipeline: ingestion -> classification -> financial parsing/analysis -> contract review ->
risk register consolidation -> report rendering. Each Claude-dependent stage after
ingestion has a --skip-* flag so the deterministic parts of the pipeline can be exercised
(or a partial run resumed) without spending API calls.
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
EXECUTION_DIR = REPO_ROOT / "execution"
if str(EXECUTION_DIR) not in sys.path:
    sys.path.insert(0, str(EXECUTION_DIR))

from dotenv import load_dotenv  # noqa: E402

from dd_agent.classification.classifier import classify_document_index  # noqa: E402
from dd_agent.config import load_settings  # noqa: E402
from dd_agent.financial.analysis import flag_financial_anomalies  # noqa: E402
from dd_agent.financial.parser import parse_financial_statements  # noqa: E402
from dd_agent.ingestion.index import build_document_index, save_document_index  # noqa: E402
from dd_agent.legal.contract_review import build_contracts_register  # noqa: E402
from dd_agent.reporting.docx_report import render_docx_report  # noqa: E402
from dd_agent.reporting.markdown_report import render_markdown_report  # noqa: E402
from dd_agent.risk.risk_register import build_risk_register  # noqa: E402
from dd_agent.schemas.common import DocumentStatus  # noqa: E402
from dd_agent.schemas.contracts import ContractsRegister  # noqa: E402
from dd_agent.schemas.financial import FinancialSummary  # noqa: E402
from dd_agent.schemas.risk_register import RiskRegister  # noqa: E402

logger = logging.getLogger("dd_agent.cli")


def _make_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _save_json(model, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(model.model_dump_json(indent=2), encoding="utf-8")
    logger.info("Wrote %s", path)


def _print_index_summary(index) -> None:
    by_category: dict[str, int] = {}
    by_status: dict[str, int] = {}
    for entry in index.entries:
        by_category[entry.category.value] = by_category.get(entry.category.value, 0) + 1
        by_status[entry.status.value] = by_status.get(entry.status.value, 0) + 1

    print(f"\nDocument index: {len(index.entries)} documents")
    print("By category:")
    for category, count in sorted(by_category.items(), key=lambda kv: -kv[1]):
        print(f"  {category:<24} {count}")
    print("By status:")
    for status, count in sorted(by_status.items(), key=lambda kv: -kv[1]):
        print(f"  {status:<24} {count}")

    locked_or_unreadable = [
        e for e in index.entries if e.status in (DocumentStatus.LOCKED, DocumentStatus.UNREADABLE)
    ]
    if locked_or_unreadable:
        print(f"\n{len(locked_or_unreadable)} document(s) need attention (locked/unreadable):")
        for entry in locked_or_unreadable:
            print(f"  [{entry.status.value}] {entry.file_path} — {entry.error or ''}")


def _print_pipeline_summary(financial_summary, contracts_register, risk_register) -> None:
    print(f"\nFinancial periods extracted: {len(financial_summary.periods)}")
    print(f"Financial anomalies flagged: {len(financial_summary.anomalies)}")
    print(f"Contracts reviewed: {len(contracts_register.entries)}")
    print(f"Risk register items: {len(risk_register.items)}")
    if risk_register.items:
        by_severity: dict[str, int] = {}
        for item in risk_register.items:
            by_severity[item.severity.value] = by_severity.get(item.severity.value, 0) + 1
        print("  " + ", ".join(f"{v} {k}" for k, v in sorted(by_severity.items(), key=lambda kv: -kv[1])))


def main() -> int:
    parser = argparse.ArgumentParser(description="Due Diligence Agent")
    parser.add_argument("--input", required=True, help="Path to the data room folder")
    parser.add_argument("--output", required=True, help="Path to write report artifacts")
    parser.add_argument("--config", default=None, help="Path to config.yaml (default: repo root)")
    parser.add_argument("--skip-classification", action="store_true", help="Skip Claude document classification")
    parser.add_argument("--skip-financial", action="store_true", help="Skip Claude financial statement parsing")
    parser.add_argument("--skip-legal", action="store_true", help="Skip Claude contract review")
    parser.add_argument("--skip-risk", action="store_true", help="Skip Claude risk register phrasing")
    parser.add_argument("--skip-report", action="store_true", help="Write JSON artifacts only, skip report rendering")
    parser.add_argument("--format", choices=["md", "docx", "both"], default="both", help="Report output format(s)")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )

    load_dotenv(REPO_ROOT / ".env")
    settings = load_settings(args.config)

    dataroom_root = Path(args.input).resolve()
    output_dir = Path(args.output).resolve()
    run_id = _make_run_id()
    tmp_dir = REPO_ROOT / ".tmp" / run_id

    logger.info("Run %s: ingesting %s", run_id, dataroom_root)
    index = build_document_index(
        dataroom_root=dataroom_root, run_id=run_id, tmp_dir=tmp_dir, ingestion_config=settings.ingestion
    )

    if args.skip_classification:
        logger.info("Skipping classification (--skip-classification)")
    else:
        logger.info("Classifying documents via %s", settings.model.name)
        index = classify_document_index(index, settings.model)

    save_document_index(index, output_dir)
    _print_index_summary(index)

    financial_summary = FinancialSummary(run_id=run_id)
    if args.skip_financial:
        logger.info("Skipping financial parsing (--skip-financial)")
    else:
        logger.info("Parsing financial statements via %s", settings.model.name)
        financial_summary = parse_financial_statements(index, settings.model)
        financial_summary = flag_financial_anomalies(financial_summary, settings.materiality)
    _save_json(financial_summary, output_dir / "financial_summary.json")

    contracts_register = ContractsRegister(run_id=run_id)
    if args.skip_legal:
        logger.info("Skipping contract review (--skip-legal)")
    else:
        logger.info("Reviewing contracts via %s", settings.model.name)
        contracts_register = build_contracts_register(index, settings.model)
    _save_json(contracts_register, output_dir / "contracts_register.json")

    risk_register = RiskRegister(run_id=run_id)
    if args.skip_risk:
        logger.info("Skipping risk register (--skip-risk)")
    else:
        logger.info("Building risk register via %s", settings.model.name)
        risk_register = build_risk_register(index, financial_summary, contracts_register, settings.model)
    _save_json(risk_register, output_dir / "risk_register.json")

    _print_pipeline_summary(financial_summary, contracts_register, risk_register)

    if args.skip_report:
        logger.info("Skipping report rendering (--skip-report)")
    else:
        if args.format in ("md", "both"):
            path = render_markdown_report(
                index, risk_register, contracts_register, financial_summary, output_dir / "DD_Summary.md"
            )
            print(f"\nWrote {path}")
        if args.format in ("docx", "both"):
            path = render_docx_report(
                index, risk_register, contracts_register, financial_summary, settings.report,
                output_dir / "DD_Summary.docx",
            )
            print(f"Wrote {path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
