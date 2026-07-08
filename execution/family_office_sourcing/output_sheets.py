"""Upserts CandidateRecords into a Google Sheet CRM.

This is optional infrastructure, not the core deliverable (the docx report always works
without any Google setup) — so unlike dd_agent's "missing ANTHROPIC_API_KEY fails the whole
run fast," a missing/unconfigured Google OAuth setup here should be a clear skip, not a
crash, since the run's other output already succeeded.

Upsert, never append-blind: re-running the sourcing pass must not duplicate rows or clobber
the `Status`/`Notes` columns an analyst has already filled in by hand (per directive edge
cases). Rows are keyed on a hidden `Candidate ID` column.
"""
from __future__ import annotations

import logging
from pathlib import Path

from family_office_sourcing.schemas import CandidateRecord, SourcingRun

logger = logging.getLogger("family_office_sourcing.output_sheets")

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

_BASE_HEADERS = [
    "Candidate ID", "Name", "Tier", "City", "State", "Principal", "Sector Signals",
    "Website", "Confidence", "Mode", "Source(s)", "Note", "Last Refreshed",
]


class SheetsNotConfigured(RuntimeError):
    """Raised when credentials.json/token.json aren't present — caller should skip, not crash."""


def _authenticate(repo_root: Path):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    token_path = repo_root / "token.json"
    creds_path = repo_root / "credentials.json"
    if not creds_path.exists():
        raise SheetsNotConfigured(
            f"{creds_path} not found. Add Google OAuth credentials to enable Sheet sync, "
            "or run with --skip-sheets."
        )

    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), SCOPES)
            creds = flow.run_local_server(port=0)
        token_path.write_text(creds.to_json(), encoding="utf-8")
    return creds


def _row_for(record: CandidateRecord) -> list[str]:
    sources = "; ".join(sorted({s.source_name for s in record.sources}))
    note = record.exclusion_reason or (record.enrichment.rationale if record.enrichment else "")
    base = {
        "Candidate ID": record.candidate_id,
        "Name": record.display_name,
        "Tier": record.priority_tier.value,
        "City": record.city or "",
        "State": record.state or "",
        "Principal": record.principal or "",
        "Sector Signals": ", ".join(record.sector_signals),
        "Website": record.website or "",
        "Confidence": record.confidence.value,
        "Mode": record.investment_mode.value,
        "Source(s)": sources,
        "Note": note,
        "Last Refreshed": record.last_refreshed.isoformat(),
    }
    return [base[h] for h in _BASE_HEADERS]


def sync_to_sheet(
    run: SourcingRun,
    spreadsheet_id: str,
    sheet_name: str,
    preserve_columns: list[str],
    repo_root: Path,
) -> None:
    from googleapiclient.discovery import build

    creds = _authenticate(repo_root)
    service = build("sheets", "v4", credentials=creds)
    full_headers = _BASE_HEADERS + [c for c in preserve_columns if c not in _BASE_HEADERS]

    existing = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id, range=f"{sheet_name}!A1:Z10000"
    ).execute().get("values", [])

    if not existing:
        service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id, range=f"{sheet_name}!A1",
            valueInputOption="RAW", body={"values": [full_headers]},
        ).execute()
        existing = [full_headers]

    header_row = existing[0]
    id_col = header_row.index("Candidate ID") if "Candidate ID" in header_row else 0
    existing_by_id = {row[id_col]: (idx, row) for idx, row in enumerate(existing[1:], start=2) if len(row) > id_col}

    updates = []
    appends = []
    for record in run.records:
        preserved_values = {}
        if record.candidate_id in existing_by_id:
            _, existing_row = existing_by_id[record.candidate_id]
            for col in preserve_columns:
                if col in header_row:
                    col_idx = header_row.index(col)
                    if col_idx < len(existing_row):
                        preserved_values[col] = existing_row[col_idx]

        base_row = _row_for(record)
        full_row = base_row + [preserved_values.get(c, "") for c in preserve_columns if c not in _BASE_HEADERS]

        if record.candidate_id in existing_by_id:
            row_idx, _ = existing_by_id[record.candidate_id]
            updates.append({"range": f"{sheet_name}!A{row_idx}", "values": [full_row]})
        else:
            appends.append(full_row)

    if updates:
        service.spreadsheets().values().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"valueInputOption": "RAW", "data": updates},
        ).execute()
    if appends:
        service.spreadsheets().values().append(
            spreadsheetId=spreadsheet_id, range=f"{sheet_name}!A1",
            valueInputOption="RAW", insertDataOption="INSERT_ROWS",
            body={"values": appends},
        ).execute()

    logger.info("Sheet sync: %d updated, %d appended", len(updates), len(appends))
