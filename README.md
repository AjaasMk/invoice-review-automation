# Invoice Decision Pipeline

An end-to-end invoice processing pipeline: an invoice arrives (email, folder drop, or upload), a human gates it at intake, then the system extracts, validates, matches it to a purchase order, and produces a reasoned decision — `AUTO_APPROVE`, `NEEDS_REVIEW`, or `REJECT` — with a full evidence trail. Built for the Zamp AI Solutions Associate case study (PS-1).

Design rationale and architecture: [`docs/superpowers/specs/2026-09-20-invoice-decision-pipeline-design.md`](docs/superpowers/specs/2026-09-20-invoice-decision-pipeline-design.md).

## Current status

- **Application:** FastAPI service with a responsive review workspace, live stage updates, run history, vendor/PO database view, and a development-only reset control. Invoice extraction is configured for DeepSeek.
- **Intake:** Manual upload, watched-folder intake, and Gmail IMAP intake are implemented. Gmail processing is deliberately restricted to messages labelled `Invoice Review Queue`; an employee applies that label before the system reads the attachment.
- **Controls:** Structured extraction is separated from deterministic validation, vendor/PO matching, cumulative PO-balance checks, and duplicate detection. Exact file duplicates are stopped before model processing; content-level duplicates are routed to review with an explanation. The exception drawer records human resolutions, supports vendor/PO reference-data creation, and can hold, reject, archive, or close an exception without erasing its original evidence.
- **Demo data:** Five vendors, twenty purchase orders, regression fixtures, and three DB-matched demo invoices are included.
- **Deployment:** Docker packaging and a Railway deployment guide are ready. The source is published in the private [GitHub repository](https://github.com/AjaasMk/invoice-review-automation); Railway connection and public-domain verification are the remaining release steps.

GitHub Actions workflow suggestions can be ignored for this project. They are generic package/lint templates; Railway builds and runs the included `Dockerfile` directly.

## Setup

```bash
python -m pip install -r requirements.txt
cp .env.example .env
```

On Windows PowerShell, use `Copy-Item .env.example .env` for the second command.

Edit `.env` — the DeepSeek model reads the invoice; it never decides approve/reject:
- `LLM_PROVIDER=deepseek`
- `DEEPSEEK_API_KEY` — your DeepSeek API key.
- `DEEPSEEK_MODEL=deepseek-flash` — used for text PDFs and rendered scanned/image invoices.
- `DEMO_RESET_TOKEN` — leave blank locally. Set a strong private value in Railway; enter it in the Settings reset dialog only when you want to reset the hosted demo.
- `GMAIL_IMAP_USER` / `GMAIL_IMAP_APP_PASSWORD` — optional. Leave blank to run on folder/upload intake only. If set, needs a Gmail [App Password](https://myaccount.google.com/apppasswords) (requires 2-Step Verification), not the account password.
- `GMAIL_IMAP_MAILBOX` — defaults to `INBOX`. For the review workflow, create a Gmail label named `Invoice Review Queue`, set this value to that exact label name, and have an employee apply it to invoice mail. The app polls only that label, including messages that have already been read.

Generate the test fixtures (happy path + all 4 edge cases) if `data/invoices/` is empty:

```bash
python -m data.generate_fixtures
```

## Run it

```bash
python -m web.server
```

No API key handy? `python preview_server.py` runs the same UI on fake extraction/triage clients (port 8123) so you can check layout and flow without spending a real call.

Open `http://localhost:8000`. The workspace includes:
- **Inbox** — upload a PDF/image or monitor the configured Gmail label and watched folder.
- **Review Queue** — approve/reject incoming documents before they consume an extraction call.
- **Live Run** — watch each pipeline stage move from started to completed (or failed) over SSE as it executes.
- **Invoices** — filterable history with invoice, vendor, amount, status, decision, and reason codes; click a row to reopen its full trace.
- **Live database** — inspect the five vendors and twenty purchase orders used by matching.

## Verify it

```bash
python -m pytest tests/ -v          # unit and integration suite
python -m evals.run_evals           # structured JSON pass/fail across happy path + all 4 edge cases
```

`run_evals` exits 0 only if every case matches its expected outcome and reason codes exactly — this is the regression gate, not just the unit tests.

`python live_smoke_test.py` runs the same 7 cases against your *real* configured model (costs real API calls) instead of canned responses — the thing to re-run after any change to the extraction prompt, a model swap, or before the interview, to confirm real model behavior still lands on the right decisions.

## Rerun any stage in isolation

Every stage's output is persisted per run. To re-evaluate just the matching or decision logic after a rule/config change, without re-running extraction (no repeat API cost):

```bash
python -m pipeline.rerun --run <run_id> --from po_match
python -m pipeline.rerun --run <run_id> --from decide
```

## Architecture, in one paragraph

FastAPI serves the UI and API over SSE. LangGraph orchestrates the pipeline as a checkpointed state machine: triage → **human gate (durable interrupt)** → extract (pdfplumber for machine-readable PDFs, configured vision model for scans/images) → normalize → validate → vendor resolve (fuzzy match) → PO match (candidate retrieval + weighted scoring + cumulative balance tracking + duplicate detection) → decide (pure, config-driven rules). SQLite stores runs, per-stage artifacts, vendors, and POs. The model only ever extracts structured fields — every approve/reject/flag decision is a deterministic function over those fields, so it's reproducible and explainable.

Scanned PDFs are rendered page by page and all pages are provided to vision extraction. PO candidates must match the resolved vendor and currency; explicit PO references cannot silently fall back to another PO, while reference-free matches must fit the configured date and amount windows.

Every incoming attachment also receives a SHA-256 fingerprint. An exact re-upload is stopped before triage or extraction and recorded as `NEEDS_REVIEW` with `EXACT_FILE_DUPLICATE`; separately, the existing vendor/invoice-number/date/amount comparison catches re-sent invoices whose PDF bytes differ.

## Deploy and demonstrate

- Docker/cloud-host instructions: [`docs/deployment.md`](docs/deployment.md)
- Five-minute recording and interview sequence: [`docs/demo-runbook.md`](docs/demo-runbook.md)
- Railway release checklist: create one service from this repository, attach a persistent volume at `/app/runs`, set health check `/api/health`, add the model/Gmail values as Railway secrets, and generate a public domain. Keep one replica because SQLite is used for the demo database.

## Edge cases (all verified in `evals/run_evals.py`)

| Case | What it proves |
|---|---|
| Split billing (`edge_a_*`) | One PO invoiced across two partial invoices — the second is judged against the *remaining* balance, and a partial invoice is not wrongly flagged just for being less than the full PO amount. |
| Tax mismatch (`edge_b_*`) | Invoice total looks ~8% over the PO because the PO is tax-exclusive and the invoice is tax-inclusive — comparison basis is chosen from the *PO's* stated treatment, not guessed from the invoice. |
| Near-duplicate (`edge_c_*`) | Same vendor, same amount, different invoice number, days apart — flagged `NEEDS_REVIEW` with the specific prior invoice cited, never silently approved or silently blocked. |
| Degraded scan (`edge_d_*`) | Extraction confidence collapses below the floor — the system refuses to guess and names exactly which fields are missing. |

## Known limitations / assumptions

- Test data (vendors, POs, invoices) is generated, not real — per the case study's own guidance.
- Single currency (USD) across all fixtures; no multi-currency conversion.
- "Approved vendor" is a boolean flag on the vendor master; no external verification.
- `RealImapClient` is not covered by the automated test suite because it requires live Gmail credentials. Use the configured review label for a manual intake check before recording the demo.
- Amount tolerance, duplicate detection window, and confidence floor are illustrative defaults in `pipeline/config.py`, not derived from real historical data — callable out live if asked.
- Real-model testing surfaced something worth knowing going in: a vision model shown a genuinely illegible scan doesn't reliably self-report low confidence — it can fabricate a complete, plausible-looking invoice instead of admitting it can't read one. `EXTRACTION_INSTRUCTIONS` explicitly forbids this now, and `parse_json_response` degrades to an all-null result (rather than crashing) when a model abstains in prose instead of JSON — but this is a real, ongoing model-behavior risk worth stating plainly, not a solved problem.
- `extraction_confidence` is a conservative completeness heuristic over critical fields, not a calibrated probability from the model.
- Run `python live_smoke_test.py` with the configured DeepSeek key before recording. It makes real model calls and is the final check that the extraction provider is behaving as expected.
