# Directive: Indian Family Office Sourcing

## Goal
Build and maintain a prioritized, sourced, continuously-enriched list of Indian family
offices — including ones with no public directory listing — as prospective LP/co-investor
contacts for EQUINOVA. The system's edge is combining structured/database sources with
public-record and open-web signal discovery (including LinkedIn, via search-engine-indexed
results only) to surface single-family offices (SFOs) that aren't proactively marketing
themselves, which is most of them.

**Source roles (updated 2026-07-07)**: `--web-signals` (open web + LinkedIn discovery via
`WebSearch`) is the **primary** source — it's the only one that doesn't depend on a specific
paid database and is what "difficult to reach" actually points at. `--tracxn-raw` is an
**optional structured cross-check** when available, not a hard dependency. Trust in scoring
now comes from either path (a curated database, or an LLM-verified plausible signal) — see
`scoring.py`'s `enrichment_confirmed_weight`, added specifically so a LinkedIn-only run can
still produce Tier A candidates once `enrichment.py` verifies them, rather than every
unconfirmed candidate being permanently capped below a Tracxn-sourced one.

**The four filters (added 2026-07-08, from user notes on a similar product's design):**
1. **Geography** — `--city`/`--state` CLI filters (exact match, case-insensitive) plus
   `--group-by state` for an all-India state-by-state cut. Already existed before this note.
2. **Sector/"focus keyword"** — `config.yaml`'s `target_sectors` list drives the
   `sector_fit_weight` scoring bonus; combine with `--city`/`--state` for a sector+geography
   cut (e.g. fintech offices in Mumbai). Already existed before this note.
3. **Family office type exclusion** — `config.yaml`'s `exclude_office_type_keywords`,
   applied in `scoring.py` to **unconfirmed candidates only**. Matches VC-firm/bank/wealth-
   manager/advisory-firm language and pins matches to Tier C with the match reason surfaced
   in the report's Note column. `--exclude-flagged` drops them entirely instead of just
   demoting. Never applied to Tracxn-confirmed records — see the field comment on
   `CandidateRecord.exclusion_reason` for why.
4. **LP/Allocator vs. Direct/Co-Investor mode** — `config.yaml`'s `lp_allocator_keywords`/
   `direct_investor_keywords`, deterministic classification into `InvestmentMode`, filterable
   via `--investment-mode {lp,direct}`. Purely additive metadata (not an exclusion); most
   candidates classify `Unknown` given how terse the underlying descriptions are — verified
   live at 0/123 classified either way against the current test dataset, so treat this as a
   real but currently low-coverage signal, not a reliable primary filter yet.

**Every candidate must be sourced and confidence-labeled.** This is a prospect list feeding
real business relationships, not a demo — a fabricated or misidentified "family office" costs
an analyst's time and the firm's credibility with a warm-introduction network. Nothing in the
output may assert a fact (principal name, sector focus, contact detail) without a citation
back to where it came from.

## When to use
User asks to source, find, or refresh a list of Indian family office investors, or asks
"who are the family offices we haven't reached yet."

## Inputs
- Tracxn MCP (connected in this environment as `mcp__ac1bf81d-e684-44c4-b7f6-4dc8a6756a84__*`)
  — structured investor + legal-entity (MCA) database. Query via the orchestrator (Claude),
  not a Python script — there is no standalone Tracxn API key in `.env`, only MCP access in
  this session. Must call `load_prompt` once per conversation before any data tool, per the
  MCP's own workflow instructions.
- SEBI AIF registry (public, sebi.gov.in) — fetched via `WebFetch` by the orchestrator.
  Many Category I/II/III AIFs are sponsored by a single wealthy family; the sponsor name is
  a discovery signal for offices Tracxn hasn't tagged yet, not a confirmed family office.
- Open web / news search (via `WebSearch`) — signal discovery for family offices not yet in
  any structured database: promoter names + "family office" / "investment office" / "family
  trust", exit/liquidity events (a large stake sale often precedes a new SFO).
- LinkedIn — **discovery only via public search-engine-indexed results** (`WebSearch` with
  `site:linkedin.com`), never automated scraping or login-based access (ToS + `robots.txt`
  compliance). Produces leads for a human to verify, not confirmed records.
- `config.yaml` → `family_office_sourcing:` section (sector/ticket-size fit weights, scoring
  thresholds).
- `.env`: `ANTHROPIC_API_KEY` (candidate plausibility assessment for unconfirmed
  signals only — Tracxn-sourced records skip this, they're already confirmed).
- `credentials.json` / `token.json` (repo root, Google OAuth) — required only for the Google
  Sheet CRM sync; not required for the docx report.

## Tools / scripts
Orchestrated via `family_office_sourcing.py` at the repo root. The orchestrator (Claude) does
the live data-fetching (Tracxn MCP calls, SEBI WebFetch, WebSearch) and saves raw JSON to
`.tmp/<run_id>/`; the Python pipeline does everything after that deterministically:

```
python family_office_sourcing.py --tracxn-raw .tmp/<run_id>/tracxn_raw.json \
    [--sebi-raw .tmp/<run_id>/sebi_aif.json] [--web-signals .tmp/<run_id>/web_signals.json] \
    --output .tmp/<run_id>/ [--skip-enrichment] [--skip-sheets] [--format docx|csv|both]
```

All in `execution/family_office_sourcing/`:
1. `ingest.py` — normalizes each raw source (Tracxn investor records, SEBI AIF sponsor
   rows, web search hits) into a common `Candidate` shape, tagging `source_type` and
   `confidence` (`confirmed` for Tracxn, `unconfirmed` for SEBI/web).
2. `matching.py` — fuzzy-dedupes candidates across sources by normalized name
   (`rapidfuzz`), merging evidence onto one `CandidateRecord` per real-world office rather
   than emitting near-duplicates.
3. `enrichment.py` — one batched Claude call (via `dd_agent.llm.claude_client.structured_call`
   — reused as-is, not duplicated) that only runs on `unconfirmed` candidates: assesses
   plausibility that a signal actually represents a family office, phrases a rationale.
   Never invents contact details or principal names not present in the source snippet.
4. `scoring.py` — deterministic priority tiering (A/B/C) from `config.yaml` weights: sector
   fit, city, confirmed vs. unconfirmed, recency of any investment activity.
5. `output_docx.py` — house-style report (reuses `dd_agent.reporting.docx_report`'s private
   styling helpers — same navy/gold/cream/Cambria palette, native Word tables).
6. `output_sheets.py` — upserts into a Google Sheet CRM by stable `candidate_id`; must never
   overwrite the human-editable `Status` / `Notes` columns on an existing row.

## Outputs
- `<output>/candidates.json` — every merged `CandidateRecord`, full evidence trail (schema:
  `execution/family_office_sourcing/schemas.py`).
- `<output>/Family_Office_Prospects.docx` — house-style report, prospects grouped by
  priority tier, each with a sourcing footnote.
- Google Sheet (if `credentials.json` present) — one row per `CandidateRecord`, columns:
  Name, Tier, City, Sector Signals, Principal (if known), Source(s), Confidence, Status,
  Notes, Last Refreshed.
- Intermediate raw JSON lives in `.tmp/<run_id>/` — regenerate anytime, never a deliverable.

## Operating Runbook (added 2026-07-08 — for whoever runs this monthly at EQUINOVA)

**Pre-flight, before the first production run:**
1. **Confirm Tracxn subscription terms permit this usage.** This system bulk-pulls the
   Single Family Offices feed and stores/exports it internally. Check with whoever manages
   the Tracxn contract that bulk extraction and internal redistribution (e.g. into a shared
   Google Sheet) is within the plan's terms before this becomes a standing process — not
   something this directive or the code can verify on its own.
2. **Anthropic API credits.** The enrichment step (LLM plausibility-checking of
   unconfirmed/LinkedIn candidates) silently no-ops without them — every unconfirmed
   candidate stays capped in Tier C. Confirm the account tied to `ANTHROPIC_API_KEY` in
   `.env` has a positive balance before relying on enrichment-driven tiering.
3. **Google OAuth `credentials.json`** (repo root) — only needed for the live Sheet CRM
   sync (`--sheet-id`). Not required for the docx/CSV path.

**Monthly refresh cadence (recommended cycle — adjust to actual deal flow):**
1. **Pull structured data.** In a Claude Code session in this repo, ask for a full India
   Single Family Offices pull from Tracxn. Note: pagination on this feed drifts because the
   underlying dataset is live (see Learnings, 2026-07-08) — a plain offset sweep stalls
   partway through. Sort explicitly by `investorName` ASC and then DESC to close the gap,
   and always dedupe by Tracxn `id` against what's already collected before merging a new
   page (this is exactly how the 110→164 gap was closed in this session — don't re-derive
   the approach from scratch, reuse it).
2. **Pull discovery signals.** Ask for `site:linkedin.com` searches for target sectors/
   cities not yet well covered (see `config.yaml` → `target_sectors`), plus general
   "family office" + city/promoter-name searches for signal discovery. A handful of
   targeted queries (5-10) per run is enough to meaningfully grow the unconfirmed pool.
3. **Run the pipeline**: `python family_office_sourcing.py --tracxn-raw <path> --web-signals
   <path> --output .tmp/<run_id>/output --format both` (enrichment runs by default unless
   `--skip-enrichment` is passed — only skip it if credits are known to be exhausted, since
   skipping means no unconfirmed candidate can ever reach Tier A this run).
4. **Review Tier A first**, then B. Tier C (including anything with `exclusion_reason` set)
   is a low-priority/needs-review bucket, not a "don't bother" bucket — a gap in the
   exclusion or enrichment keyword lists can put a real candidate there.
5. **Cut by geography/sector/investment-mode as needed** for a specific mandate — see the
   Goal section's "four filters" — rather than hand-filtering the full list.

**Verification, before any outreach (this system never does this step):**
- Confirm the office still exists / info is current (Tracxn/LinkedIn data can be stale).
- Find an actual warm-intro path — this system surfaces the office and its public source,
  never a private contact channel it doesn't have.
- Log the outreach decision (contacted / declined / needs more research) wherever EQUINOVA
  already tracks fundraising/BD activity — this system's own `Status`/`Notes` columns (Sheet
  sync) are for that, but only if the team is actually using the Sheet as the system of
  record; don't let two parallel tracking systems drift out of sync.

**Ownership**: assign one person to run the monthly refresh (steps 1-3 above) and one
(can be the same person) to own the Tier A/B verification queue. Without an owner for the
verification step, the sourcing list accumulates without ever turning into outreach —
which makes this a research exercise, not the investor-sourcing tool it's meant to be.

## Non-goals (v1)
- **No automated outreach.** This system sources and prioritizes; it does not send emails,
  connection requests, or messages. Wiring outreach automation on top of this list is a
  separate, explicitly-requested project, not an implicit next step.
- **No LinkedIn scraping/automation** — search-engine-indexed discovery only, per ToS.
- **No MCA-filing-based heuristic discovery** (e.g., flagging any "XYZ Family Trust" /
  promoter holding company as a candidate purely from `search_legal_entities` name patterns)
  — too high a false-positive rate for v1. Stubbed for a later increment once there's a
  labeled sample to tune the heuristic against.
- **No guaranteed contact info.** Tracxn's `contactDetail` is frequently just a
  yes/no phone-number flag, not the number itself. The system surfaces the office and its
  public source (often a website); finding a warm intro path is a manual next step, not
  something this tool fabricates.

## Edge cases
- **Tracxn feed has no separate "Multi Family Office" category** — confirmed via
  `search_sectors` (query "family office", type FEED) — only "Single Family Offices"
  (`feedId: 57dd1fbae4b0fca9d4b51fe4`) exists as a dedicated feed. Multi-family offices are
  not reliably separable from wealth managers/asset managers in this taxonomy; don't assume
  a query against "Asset Management" or "Limited Partners" feeds returns MFOs specifically.
- **`investorType` filter is not where the family-office tag lives** — `investorType` only
  has broad values like "Institutional Investor" / "Angel Investor". The actual
  classification is in the `type` array (`{"name": "Single Family Office"}`) and in
  `primaryTaxonomy`, reachable by filtering `investorFeedId`. Filtering by
  `investorType: ["Family Office"]` silently returns zero results (no error) — don't trust
  an empty result from a guessed enum value as "no data exists."
  Verified 2026-07-06 against Premji Invest (a well-known Indian SFO).
  investorCountry: India alone returns real data (71,185 investors; India SFO feed alone:
  164) — coverage is usable, not a stub data source.
- **Contact data is sparse by design in the underlying database**, consistent with "hard to
  reach" — don't let enrichment "fill in" a plausible-looking email/phone that wasn't in the
  source. Missing means missing; the report should say so, not omit the row.
- **SEBI AIF sponsor name ≠ confirmed family office** — an AIF sponsor could be a corporate
  or a professional fund manager, not a family. `enrichment.py` must down-weight or flag
  low-plausibility sponsor matches rather than promoting every AIF sponsor to a candidate.
- **Duplicate across sources** (Tracxn tags it, and a web search also surfaces it under a
  slightly different name, e.g. "Premji Invest" vs "Azim Premji Family Office"): `matching.py`
  merges these into one record and keeps every source in the evidence trail rather than
  emitting two rows the analyst has to manually notice are the same entity.
- **Re-running the sourcing pass**: `output_sheets.py` must upsert by `candidate_id`
  (stable hash of normalized name), never append blindly — otherwise every refresh
  duplicates the whole sheet and destroys any `Status`/`Notes` an analyst already filled in.

## Learnings (update this section as the system is used)
- **2026-07-06, scoped during design**: Tracxn MCP access in this environment has no
  corresponding REST API key in `.env` — it's only callable by the orchestrating agent
  in-session, not from a standalone Python script. This is why the pipeline is split at the
  raw-JSON boundary: live data-fetching is an orchestration-layer responsibility here, not
  execution-layer, which is a deliberate deviation from the "scripts do all API calls"
  default in the top-level agent instructions, forced by how the MCP is exposed.
- **2026-07-06, bug found + fixed during first live smoke test**: `ingest.py`'s original
  principal-extraction regex (`\bof\s+([A-Z]...),`) matched *any* "of Capitalized Text,"
  pattern, not just family-office-specific phrasing. Against real Tracxn data it produced a
  false "principal" for Brescon — shortDescription "Provider of BPO services for
  healthcare, logistics..." — because the acronym "BPO" looked enough like a name to match.
  Fixed by anchoring the pattern specifically on "(family) office of <Name>" (see
  `ingest.py`'s `_PRINCIPAL_PATTERN`). Re-verified against 10 real description strings
  pulled live from Tracxn before trusting it again — this class of bug (a plausible-looking
  but wrong extracted fact) is exactly what the directive's "nothing asserted without a
  citation" rule exists to catch, so don't skip re-verification after touching this regex.
- **2026-07-06, bug found + fixed during first live smoke test**: `enrichment.py` only
  caught `StructuredCallError` around the Claude call, not the underlying `anthropic`
  client's own exceptions (e.g. `BadRequestError` for a $0 credit balance, the same billing
  condition dd_agent's directive already documented). Since enrichment is optional — the
  confirmed-source majority of a run is already valid without it — a mid-call API failure
  must degrade to "no plausibility assessment," not discard an otherwise-successful run of
  100 confirmed candidates. Broadened to catch `Exception` generically around that one call.
- **2026-07-06, verified live**: pulled the real India Single Family Offices feed (100 of
  164 records, 2 pages) and ran the full pipeline end-to-end (ingest → dedupe → score →
  docx/CSV output) with no crashes after the two fixes above. Confirmed real behavior:
  (1) `matching.py` correctly merged two full-duplicate Tracxn records that share the exact
  name "Shekama Family Trust" but different Tracxn IDs (61c69946... and 61a60281...) — worth
  knowing Tracxn's own data has this kind of duplication upstream; (2) it also correctly
  merged "Kemfin" and "Kemfin Family Office" as an alias pair (rapidfuzz score above the 88
  threshold); (3) it did **not** merge a web-search hit titled "Raay Investments (Amit Patni
  family office)" onto the already-confirmed Tracxn record "RAAY Global Investments" —
  token_sort_ratio scores only 46 because the noisy web title dilutes the match. Don't raise
  the dedupe threshold to chase this; a lower threshold risks false-positive merges across
  unrelated offices. Cross-source entity resolution for noisy titles needs a smarter
  approach (LLM-assisted resolution, or normalizing web hit titles before matching) as a
  follow-up, not a threshold tweak.
- **2026-07-06, noted, not yet investigated**: Tracxn's own public tracker page
  (tracxn.com/d/investor-lists/single-family-offices-in-india) advertises "286 Single
  Family Offices in India (Apr, 2026)", while this MCP's `search_investors` with the same
  `investorFeedId`/`investorCountry` filter returns `total_count: 164`. Don't assume the two
  numbers should match — the public tracker may include a broader or differently-curated
  set. Worth a follow-up query (e.g. checking `investorpublishedstatus` or a broader feed)
  before treating 164 as the ceiling of what's queryable.
- **2026-07-07, source roles changed**: promoted `--web-signals` (open web + LinkedIn via
  search-engine-indexed results) from a minor supplement to the primary source; `--tracxn-raw`
  is now optional (previously required) — see the Goal section's "Source roles" note. This
  only makes sense combined with the scoring change below; a permanently-unconfirmed primary
  source with no path to Tier A would be a worse system than what came before it.
- **2026-07-07, scoring change**: added `enrichment_confirmed_weight` (default 35, vs.
  `confirmed_source_weight`'s 40) so a candidate whose only source is unconfirmed (web/
  LinkedIn) can still reach Tier A once `enrichment.py` verifies it's plausible — trust now
  comes from a curated database OR a verified-plausible evidence trail, not from Tracxn
  specifically. Without this change, promoting web/LinkedIn to primary source would have
  meant nothing sourced that way could ever outrank a Tracxn record, defeating the point.
  Note this bonus requires a *working* enrichment call (Anthropic API credits) to ever
  apply — with credits exhausted (the ongoing $0-balance issue, see below), every
  unconfirmed candidate stays capped in Tier C regardless of how strong the underlying
  evidence is. That's the correct degrade-safe behavior (per enrichment.py's own design),
  not a bug, but it means a LinkedIn-only run is a no-op for prioritization until the
  Anthropic account has credits.
- **2026-07-07, bug found + fixed during LinkedIn-source live smoke test**: pulled two real
  `site:linkedin.com` searches (person profiles + company pages) for Indian family offices
  and ran the pipeline standalone (`--web-signals` only, no `--tracxn-raw`) — confirmed it
  now runs without Tracxn at all. Combining with the existing Tracxn dataset should have
  merged "Raintree Family Office | LinkedIn" onto Tracxn's confirmed "Raintree" (identical
  entity) but didn't: `_normalize_name` didn't strip the "| LinkedIn" site-branding suffix
  LinkedIn search results carry, so it normalized to "raintree linkedin" instead of
  "raintree" and scored below the dedupe threshold. Fixed by adding "linkedin" to
  `matching.py`'s `_NOISE_WORDS`. Re-verified live after the fix: the merge now happens
  correctly, while "Narotam Sekhsaria Family Office" (LinkedIn) still correctly stays
  separate from Tracxn's "NSFO" — an acronym-vs-full-name pair is a genuine fuzzy-matching
  limitation (same class as the "RAAY Global Investments" / "Raay Investments" case above),
  not something this fix was meant to catch.
- **2026-07-08, added**: `--city` and `--state` filters on `family_office_sourcing.py`
  (case-insensitive exact match against `CandidateRecord.city`/`.state`, combinable,
  city-suffixed output filenames) — added after being asked for the same city-scoped cut
  twice in a row (Mumbai, then Delhi); state filter added immediately after for a
  Maharashtra-wide cut. Reuses the existing docx/CSV renderers on a filtered `SourcingRun`
  rather than a one-off script, so every future city/state cut is a documented, repeatable
  CLI invocation instead of ad hoc code.
- **2026-07-08, live pagination note**: pulling a third Tracxn page for the same
  `investorFeedId`/`investorCountry` query returned `total_count: 165` (up from 164 on the
  first two pages) and re-listed ~44 already-seen records alongside 10 genuinely new ones —
  confirms the underlying dataset is live and shifting between calls, so offset-based
  pagination (`from`/`size`) is not a stable partition. Any future multi-page pull must
  dedupe by Tracxn `id` against records already ingested in the same run (as done here)
  rather than assume sequential pages sum cleanly to `total_count`.
- **2026-07-08, bug found + fixed while filtering by state**: `--state Maharashtra` surfaced
  a record ("Kemfin") located in "Bengaluru, Maharashtra" — not a real place. Root cause:
  `dedupe_candidates` merged `city` and `state` **independently** via `_merge_field`, so a
  cluster containing Tracxn's "Kemfin" (state=Maharashtra, no city) and "Kemfin Family
  Office" (city=Bengaluru, state=Karnataka) picked the city from one source and the state
  from the other, synthesizing a location neither source actually stated — exactly the kind
  of unsourced assertion the directive exists to prevent, and it would have silently
  misrouted this office into every Maharashtra-scoped list going forward. Fixed by adding
  `_merge_location()`, which always takes city+state as a tied pair from one source (the
  one with a city, preferring confirmed; falling back to state-only). Re-verified live:
  "Kemfin" now correctly resolves to Bengaluru/Karnataka, and a sweep of the full 123-record
  run confirms no other record has a city without a matching state. Any field on
  `CandidateRecord` that's only meaningful *paired* with another field (location is the
  first case; watch for this if more paired fields are added later) must not go through the
  independent-per-field `_merge_field` helper.
- **2026-07-08, added**: office-type exclusion filter and LP/Allocator-vs-Direct investment
  mode classification (both deterministic keyword matching in `scoring.py`, config-driven).
  Verified live against the real 123-record dataset: correctly excluded exactly two LinkedIn-
  sourced candidates ("Credence Family Office" — matched "multi-family office services";
  "Entrust Family Office" — matched "registered investment advisor") while leaving Tracxn-
  confirmed "Goel Family Office" (whose own description says "Venture capital fund backed by
  single family office") untouched, confirming the confirmed-bypass works as designed.
  Investment-mode classification returned `Unknown` for all 123 records in this dataset —
  the keyword lists in `config.yaml` are a real but currently low-coverage signal against
  Tracxn's terse `shortDescription` text, not something to rely on as a primary filter yet.
- **2026-07-08, completed full Tracxn pull**: closed the gap from 110/~165 to a confirmed
  **164/164** (matching `total_count` exactly, zero duplicate IDs) by working around the
  pagination drift noted earlier — a plain `from`/`size` sweep kept re-returning already-seen
  records because the live dataset shifts between calls. What worked: add an explicit
  `sort: [{"sortField": "investorName", "order": "ASC"}]` (found 5 new of 64) and then
  `"DESC"` on the same field (found 49 new of 100) — the two directions cover different
  slices of a live-sorted list even when a plain offset sweep has stalled. Always dedupe by
  Tracxn `id` against everything already collected before appending a new page, exactly as
  done here (never assume a new page is additive without checking). With the full 164 +
  16 LinkedIn signals, dedup produced 174 records (9 Tier A, 152 Tier B, 13 Tier C) — no new
  instances of the location-mismatch or duplicate-display-name bugs found in a full sweep of
  the larger set, and the LP/Allocator classifier got its first real hit ("AG Ventures" —
  "Venture capital fund and limited partner investing in multiple sectors").
- **2026-07-08, environment note**: re-running the docx renderer into an already-existing
  output directory hit `PermissionError: [Errno 13]` from Word's `.docx` zip writer — the
  prior file was locked (open in Word, or mid-sync in OneDrive, both plausible given the
  repo lives in a OneDrive-synced folder). Not a code bug; writing to a fresh output
  directory succeeded immediately. If this recurs, check whether the target file is open in
  another program before assuming the renderer itself is broken.
