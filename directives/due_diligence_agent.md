# Directive: Due Diligence Agent

## Goal
Run first-pass due diligence on a VDR-style data room folder and produce a structured,
sourced DD summary (risk register, financial overview, contracts register, document index)
for an analyst to review and refine. This never replaces analyst judgment — it produces a
sourced first draft.

## When to use
User provides (or points to) a folder of deal documents and asks for a DD review, risk
register, or DD summary report.

## Inputs
- `--input <path>`: root folder of the data room (mixed PDF/DOCX/XLSX/images, nested subfolders allowed)
- `--output <path>`: destination for the generated report + supporting JSON artifacts
- `config.yaml` (repo root): materiality thresholds, DD category taxonomy, model name
- `.env`: `ANTHROPIC_API_KEY` required for classification/extraction/risk-flagging calls

## Tools / scripts
All in `execution/dd_agent/`:
1. `ingestion/scanner.py` + `ingestion/extractors.py` + `ingestion/index.py` — walk the
   data room, extract text (OCR fallback for scanned PDFs via pytesseract), build the
   document index.
2. `classification/classifier.py` — Claude call (structured JSON, schema-validated) per
   document: category, key entities, confidence.
3. `financial/parser.py` + `financial/analysis.py` — parse financial statements, compute DD
   metrics, flag anomalies. **(stubbed in v1, not yet implemented)**
4. `legal/contract_review.py` — extract contract terms, build contracts register.
   **(stubbed in v1, not yet implemented)**
5. `risk/risk_register.py` — consolidate findings from all modules into the risk register.
   **(stubbed in v1, not yet implemented)**
6. `reporting/markdown_report.py` / `reporting/docx_report.py` — render the final DD summary.
   **(stubbed in v1, not yet implemented)**

Orchestrated via `dd_agent.py` at the repo root:
```
python dd_agent.py --input /path/to/dataroom --output /path/to/report
```

## Outputs
- `<output>/document_index.json` — one entry per source document (schema:
  `execution/dd_agent/schemas/document_index.py`)
- `<output>/risk_register.json` — consolidated, sourced risk items (schema:
  `execution/dd_agent/schemas/risk_register.py`)
- `<output>/contracts_register.json` — contract terms (schema:
  `execution/dd_agent/schemas/contracts.py`)
- `<output>/financial_summary.json` — normalized 3-5yr financial view (schema:
  `execution/dd_agent/schemas/financial.py`)
- `<output>/DD_Summary.md` (and `.docx` per house style — Cambria headings, restrained
  navy/gold/cream palette, no dark/heavy themes; see `config.yaml` → `report`) — the
  human-facing report, built only from the JSON above so every line is traceable to a
  source document + page. The `.docx` must use native Word heading styles and table
  objects (not images/text blocks) for the financial summary and risk register, so they
  can be selected and pasted directly into the firm's existing DD report template.

Intermediate extraction cache (raw text per file, per-page) lives in `.tmp/<run_id>/` and
can be deleted/regenerated at any time — never treat it as a deliverable.

## Edge cases
- **Scanned/image-only PDFs**: OCR fallback (pytesseract). If OCR confidence is low, still
  index the file but flag `ocr_low_confidence: true` in the document index rather than
  silently dropping it.
- **Password-protected files**: log as `status: "locked"` in the document index, skip
  extraction, surface in the report's Open Questions section (ask management for an
  unprotected copy) rather than failing the whole run.
- **Corrupt/unreadable files**: log as `status: "unreadable"`, continue processing the rest
  of the data room. One bad file must never abort the run.
- **Duplicate documents** (same file hashed twice, or v1/v2 of same contract): keep both in
  the index, but the classifier/risk modules should prefer the most recent by filename/date
  when they conflict, and note the superseded version in `source_documents`.
- **Nothing found for a category** (e.g. no HR documents at all): the report should say so
  explicitly ("No HR documentation was provided in the data room") rather than omitting the
  section — a gap is itself a DD finding.
- **Claude output fails schema validation**: retry once with the validation error appended
  to the prompt; if it fails again, log the raw failure and skip that one flag/entry rather
  than crashing the run.

## Non-goals (v1)
- No real-time/multi-user collaboration.
- No direct VDR platform integration (Intralinks/Datasite APIs) — local folder only.
- No legal opinions — flag issues and cite source, never render a legal conclusion.

## Learnings (update this section as the system is used)
- **2026-07-02, environment**: On Windows, `python`/`pip` may resolve to a disabled
  Microsoft Store alias stub even when "installed." Confirm with `python --version` /
  `pip --version` actually printing a version, not an install-from-Store prompt. Installing
  Python 3.13 via the Microsoft Store worked fine here once done properly (pydantic v2 and
  all deps installed and ran without issue on 3.13) — python.org is not strictly required.
  Tesseract OCR is a separate binary install, not a pip package.
- **2026-07-02, bug found + fixed during smoke test**: `ingestion/extractors.py`'s scanned-PDF
  OCR fallback only caught `ImportError` around the OCR call, not the case where
  `pytesseract` is importable but the Tesseract binary itself is missing
  (`TesseractNotFoundError`). That would have crashed the entire ingestion run on the first
  scanned page instead of degrading gracefully per the "one bad file never aborts the run"
  rule above. Fixed to catch broadly and log a per-page warning instead.
- **2026-07-02, verified**: ingestion (docx/xlsx/pdf, including table cells and sheet data)
  smoke-tested clean on a sample data room. Missing `ANTHROPIC_API_KEY` correctly fails the
  whole run fast with one clear error, rather than being swallowed as a per-document
  classification failure and retried uselessly on every document — this is intentional,
  not a bug: a bad/missing key is a config problem, not a per-document extraction problem.
  Full classification pass (the actual Claude call) is not yet live-tested — needs a real
  `ANTHROPIC_API_KEY` in `.env`.
- **2026-07-02, correction**: an earlier version of this note claimed classification had
  been verified live against the Anthropic API "once the account had credits" — that was
  wrong, no such successful run actually happened in that session, only the billing error
  below. Don't record a verification that didn't happen; a re-run the same day hit the
  identical error, confirming the account still had no credits. Live classification (and
  financial/legal/risk calls) remain unverified against the real API as of this note.
- **2026-07-02, confirmed**: a $0 account balance surfaces as a 400 `invalid_request_error`
  ("Your credit balance is too low..."), not a 401 — don't assume a 400 always means a
  malformed request; check the message body.
- **2026-07-02, financial/legal/risk/reporting built**. Design choice: `risk/risk_register.py`
  collects findings (financial anomalies, contract flags, category coverage gaps)
  **deterministically in code**, with `source_documents` attached by code — the single
  batched Claude call only phrases title/description/recommended_question and is matched
  back by a code-generated `finding_id`. This guarantees nothing in the register can be
  mis-sourced by a model error, and keeps it to one LLM call per run instead of one per
  finding.
- **2026-07-02, bug found + fixed during smoke test**: `reporting/docx_report.py` crashed
  on `report_config.table.header_text` = `"cream"` — `config.yaml`'s `report.table` section
  mixes palette *names* ("navy", "cream") with a literal hex code ("1F2A44") in the same
  three keys, but the renderer treated all three as raw hex. Added `_resolve_color()` to
  resolve palette names against `report_config.palette` first, hex values pass through
  unchanged. Caught by running the empty-data CLI path (`--skip-classification
  --skip-financial --skip-legal --skip-risk`), which is worth keeping as a fast
  no-API-calls regression check whenever the report renderers change.
- **2026-07-02, verified (mocked LLM calls, no API credits spent)**: financial anomaly
  thresholds, contract flag deterministic merging (expired-term / missing-signature flags
  correctly added alongside the model-identified flag), risk register finding collection +
  sequential ID assignment + High-first severity ordering, and both report renderers (7
  sections present, 3 native tables, sourced content) all confirmed working by monkeypatching
  `structured_call` in `legal.contract_review` and `risk.risk_register` to return canned
  responses. None of the four Claude-dependent stages (classification, financial parsing,
  contract extraction, risk phrasing) have been confirmed against the real API yet — every
  live attempt so far has hit the same $0 account balance (400 `invalid_request_error`).
- **2026-07-02, verified visually**: exported the mocked-data `.docx` report through headless
  Word COM automation (`Documents.Open` -> `SaveAs(wdFormatPDF)`) and rendered the PDF pages
  to PNG via PyMuPDF to actually inspect the styling, since there's no way to eyeball Word
  output otherwise. Found two real bugs static checks couldn't catch: (1) Word's built-in
  "Title" paragraph style ships with its own default blue bottom border, unrelated to our
  palette — only the run font/color had been overridden, not the paragraph border. (2) gold
  — explicitly called for in house style — was never actually used anywhere in the renderer.
  Fixed both together: `_set_title_rule_color()` recolors that border to
  `report_config.palette.gold`, finally giving the accent color a job. Also found table
  columns were cramped/mis-wrapping (e.g. "Counterparty" splitting mid-word) because
  `_add_table` never set explicit column widths, so Word just divided space evenly
  regardless of content — added `_set_column_widths()` and per-table width specs. Re-exported
  and re-inspected after the fix to confirm, rather than assuming the fix worked. This
  headless-Word-to-PNG technique is worth reusing any time the docx renderer changes, since
  structural checks (table count, heading text) can't catch layout/color bugs.
