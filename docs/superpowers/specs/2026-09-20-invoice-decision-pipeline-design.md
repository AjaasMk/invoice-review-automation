# Invoice Decision Pipeline — Design Spec

Status: Approved · 2026-09-20
Context: Zamp AI Solutions Associate case study, PS-1 (Finance/AP). Build deadline: 2 days (compressed from the case study's nominal 1-week schedule).

## 1. Problem

A company receives vendor invoices by email as PDFs — some machine-readable, some scanned images, formatted differently by vendor. Each has to be checked against a purchase order system (approved vendors, PO amounts, tolerance thresholds, duplicate detection) and turned into a decision. The grading criteria (from the case study PDF) are:

- The process must actually run live, on real inputs, not a mockup.
- UI is graded: an intuitive interface with a **live run view** (stage-by-stage as it executes) and a **dashboard** (history/status/outputs across runs).
- 2–4 deliberately chosen, non-trivial edge cases.
- A 5-minute demo video and a live interview run.

## 2. Goals / Non-goals

**Goals**
- End-to-end: email arrives → invoice extracted → matched to PO → reasoned decision produced, with every intermediate step visible and inspectable.
- Decisions are deterministic and explainable — reproducible from the same inputs, not a black-box LLM verdict.
- Every pipeline stage is independently re-runnable from saved input, without re-running the whole pipeline or re-paying for extraction.
- Golden eval set with structured pass/fail, covering the happy path and all edge cases.

**Non-goals**
- No real payment execution, no real vendor master data, no multi-tenant auth. This is a single-user demo system.
- No production deployment (Docker/Postgres) — SQLite and a single FastAPI process, swappable later.
- No attempt to handle invoice formats beyond what the generated test set covers.

## 3. Where the human sits

Human review gates at **intake**, before processing (per your instruction), not as a second gate after matching. Consequence: a post-match exception (amount over tolerance, PO not found, suspected duplicate) has no second human checkpoint to route to. This is resolved by making the pipeline's *output itself* the reasoned artifact — `AUTO_APPROVE` / `NEEDS_REVIEW` (with reason codes) / `REJECT` — surfaced on the dashboard rather than blocking on a second approval. This matches the brief's actual ask ("a clear, reasoned decision as output"), and keeps one gate instead of two within the time budget.

## 4. Architecture

```
                 ┌─────────────┐
  IMAP poll ────►│             │
  folder drop───►│   INTAKE    ├──► IncomingDocument
  UI upload ────►│             │
                 └─────────────┘
                        │
                        ▼
                 ┌─────────────┐
                 │   TRIAGE    │  cheap check: does this look like an invoice?
                 └─────────────┘  produces a preview for the reviewer
                        │
                        ▼
                 ┌─────────────┐
                 │ HUMAN GATE  │  AWAITING_APPROVAL (durable, resumable)
                 │ approve/    │
                 │ reject      │
                 └─────────────┘
                        │ approve
                        ▼
                 ┌─────────────┐
                 │  EXTRACT    │  router: pdfplumber (text PDFs) | Claude vision (scans)
                 │  (router)   │  records which path was used
                 └─────────────┘
                        │
                        ▼
                 ┌─────────────┐
                 │  NORMALIZE  │  canonical Invoice schema; reconciles tax-inclusive/exclusive
                 └─────────────┘
                        │
                        ▼
                 ┌─────────────┐
                 │  VALIDATE   │  completeness + line-item arithmetic self-check → confidence
                 └─────────────┘
                        │
                        ▼
                 ┌─────────────┐
                 │   VENDOR    │  fuzzy resolve against vendor master + known aliases
                 │   RESOLVE   │
                 └─────────────┘
                        │
                        ▼
                 ┌─────────────┐
                 │  PO MATCH   │  candidate retrieval (vendor+ref+amount window+date window)
                 │             │  + weighted scoring; cumulative balance tracking for split billing
                 └─────────────┘
                        │
                        ▼
                 ┌─────────────┐
                 │   DECIDE    │  rules over match result → decision + reason codes
                 └─────────────┘
                        │
                        ▼
        AUTO_APPROVE | NEEDS_REVIEW[reason codes] | REJECT
        + full evidence trail (every stage's output, persisted)
```

Orchestration: **LangGraph**, so the state machine, the pause/resume at the human gate, and per-node state are first-class — not something bolted onto a linear script. Each node is a typed function: pydantic in, pydantic out.

Core stance: **the model extracts, the rules decide.** Claude reads pixels/text and turns them into structured fields. It never adjudicates approve/reject — that's a deterministic scorer with config-driven thresholds, so every decision is reproducible and explainable to a non-technical buyer.

## 5. Data contracts

```python
class IncomingDocument(BaseModel):
    doc_id: str
    source: Literal["imap", "folder", "upload"]
    received_at: datetime
    sender: str | None
    filename: str
    content_path: str


class TriageResult(BaseModel):
    looks_like_invoice: bool
    preview_text: str
    reason: str


class LineItem(BaseModel):
    description: str
    quantity: Decimal
    unit_price: Decimal
    line_total: Decimal


class Invoice(BaseModel):
    invoice_number: str | None
    invoice_date: date | None
    vendor_name_raw: str
    po_reference: str | None
    line_items: list[LineItem]
    subtotal: Decimal | None
    tax_amount: Decimal | None
    total_amount: Decimal | None
    currency: str
    extraction_path: Literal["pdf_text", "vision"]
    extraction_confidence: float
    missing_fields: list[str]


class VendorRecord(BaseModel):
    vendor_id: str
    canonical_name: str
    aliases: list[str]
    approved: bool


class PurchaseOrder(BaseModel):
    po_id: str
    vendor_id: str
    amount: Decimal
    currency: str
    issued_date: date
    tax_treatment: Literal["inclusive", "exclusive"]
    invoiced_to_date: Decimal


class MatchEvidence(BaseModel):
    label: str
    detail: str
    weight: float


class MatchResult(BaseModel):
    po: PurchaseOrder | None
    vendor: VendorRecord | None
    score: float
    evidence: list[MatchEvidence]
    comparison_amount: Decimal | None
    amount_within_tolerance: bool
    remaining_po_balance: Decimal | None
    duplicate_of: list[str]


class Decision(BaseModel):
    outcome: Literal["AUTO_APPROVE", "NEEDS_REVIEW", "REJECT"]
    reason_codes: list[str]
    explanation: str
    evidence: list[MatchEvidence]


class RunRecord(BaseModel):
    run_id: str
    doc_id: str
    status: Literal["AWAITING_APPROVAL", "REJECTED_AT_GATE", "RUNNING", "DONE", "ERROR"]
    decision: Decision | None
    created_at: datetime
    updated_at: datetime
```

Field notes: `content_path` points to the saved file on disk — large payloads never sit inline in graph state. `invoice_number` is the business key (no synthetic `invoice_id`) — duplicate and history lookups key on vendor + number + date. Tax treatment lives on `PurchaseOrder`, not `Invoice` — `Invoice` always carries both `subtotal` and `total_amount` independently, so PO Match can pick whichever basis (`comparison_amount`) the matched PO's `tax_treatment` calls for; `amount_within_tolerance` and `remaining_po_balance` are computed once, in PO Match, and `Decision` just reads them. `RunRecord.stages` (per-stage persisted payloads) lives in the `run_stages` table, not inline on the record — see §6 Storage.

## 6. Components

- **Intake** (`pipeline/intake/`) — `IntakeSource` protocol with three implementations: `ImapSource` (real Gmail poll, PDF attachments only), `FolderSource` (watches a directory), `UploadSource` (fed by the UI's drag-drop). All three yield `IncomingDocument`. IMAP is primary for the demo; folder/upload is the fallback if Gmail is unreachable live — same downstream code path, so switching is zero-risk.
- **Triage** — lightweight Claude call: "does this look like an invoice, and here's a one-paragraph preview." Feeds the gate UI so the reviewer isn't clicking blind.
- **Human Gate** — LangGraph `interrupt()`; run persists in `AWAITING_APPROVAL`. Approve resumes the graph; reject terminates with `REJECTED_AT_GATE`. Durable via checkpointing (SQLite-backed), not an in-memory paused thread — the process can restart without losing pending approvals.
- **Extraction Router** (`pipeline/extraction/router.py`) — classifies the PDF as machine-readable (has an extractable text layer) or scanned (no/garbage text layer) and dispatches to `pdf_text.py` (pdfplumber) or `vision.py` (Claude vision). Records `extraction_path` on the `Invoice`.
- **Normalize** — one canonical schema regardless of source path. Parses raw extracted fields into typed `Invoice` (dates, decimals, line items). Does not know about any PO yet — tax reconciliation happens later, in PO Match, once a PO (and its stated tax treatment) is known.
- **Validate** — checks required fields present, line items sum to subtotal within a cent, subtotal + tax = total within a cent. Produces `extraction_confidence` and `missing_fields`. Low confidence or missing critical fields (no total, no vendor) short-circuits straight to `NEEDS_REVIEW` — the pipeline refuses to guess (edge case D).
- **Vendor Resolve** — fuzzy match (rapidfuzz or equivalent, hand-rolled scoring on top) against `data/vendors.json`, using both canonical name and alias list.
- **PO Match** — candidate retrieval: same vendor, amount within tolerance window OR PO reference explicitly cited, date within a reasonable window. Weighted scorer combines: explicit PO reference match (highest weight), amount proximity to remaining balance, date proximity. Once a PO is selected, **the PO's `tax_treatment` decides the comparison basis**: if the PO is tax-exclusive, compare `invoice.subtotal` to the PO's remaining balance; if tax-inclusive, compare `invoice.total_amount` (this is what makes edge case B work — the reconciliation is anchored to the PO's own stated basis, not a guess derived from the invoice). Tracks `invoiced_to_date` cumulative balance per PO so a second partial invoice against an already-partially-invoiced PO is evaluated against *remaining* balance, not the PO's original total (edge case A) — the same tolerance check against remaining balance naturally distinguishes a plain mismatch (`AMOUNT_OVER_TOLERANCE`, nothing invoiced yet) from an over-the-balance split invoice (`PO_BALANCE_EXCEEDED`, something already invoiced). Also runs a duplicate check: same vendor + amount within a small window + invoice dates within N days + different invoice number → flags `duplicate_of` rather than silently accepting or blocking (edge case C).
- **Decide** — pure function over `Invoice` + `MatchResult` + config-driven rules → `Decision`. It is self-contained: given the same two inputs it always returns the same decision, whether called from the graph, a unit test, or a golden eval — no hidden dependency on where in the pipeline it's invoked from. Rule evaluation order: (1) low confidence / missing required fields → `NEEDS_REVIEW` with `LOW_EXTRACTION_CONFIDENCE` / `MISSING_REQUIRED_FIELD`, checked first and independent of matching; (2) no vendor match → `NEEDS_REVIEW` with `VENDOR_NOT_FOUND`; (3) vendor matched but not approved → `REJECT` with `VENDOR_NOT_APPROVED` (a hard business block, terminal); (4) duplicate suspected → `NEEDS_REVIEW` with `DUPLICATE_SUSPECTED`; (5) no PO found → `NEEDS_REVIEW` with `PO_NOT_FOUND`; (6) PO found but amount outside tolerance → `NEEDS_REVIEW` with `AMOUNT_OVER_TOLERANCE` (nothing invoiced against this PO yet) or `PO_BALANCE_EXCEEDED` (some balance already invoiced — this is the split-billing distinction); otherwise `AUTO_APPROVE`. Reason codes accumulate — an invoice can carry more than one simultaneously (e.g. `PO_NOT_FOUND` and `LOW_EXTRACTION_CONFIDENCE` together) except that `VENDOR_NOT_APPROVED` is terminal and short-circuits further checks. Tolerances and thresholds live in `pipeline/config.py` as data, not scattered magic numbers in logic — inspectable and changeable live during the interview if asked.
- **Storage** (`pipeline/storage.py`) — SQLite via the stdlib `sqlite3` module behind a small typed repository (no ORM). Tables: `runs`, `run_stages`, `pos` (with live `invoiced_to_date`), `vendors`.
- **Web** (`web/server.py`) — FastAPI. Three views over one static SPA: **Gate** (pending approvals, preview, approve/reject buttons), **Live Run** (SSE stream of stage transitions for a run, click any stage to see its full JSON payload), **Dashboard** (all runs, filterable by status/vendor/decision, drill into any run's full evidence trail).
- **Rerun CLI** (`pipeline/rerun.py`) — every stage's output is persisted to `runs/<run_id>/<seq>-<stage>.json` as it completes. `python -m pipeline.rerun --run <id> --from <stage>` replays from any stage using saved upstream inputs — e.g. re-run just PO matching after a tolerance-config change, without re-extracting.
- **Evals** (`evals/`) — golden cases, each pinning an input document to an expected `outcome` + expected `reason_codes`. Runner emits JSON: `{"case": ..., "pass": bool, "expected": ..., "actual": ...}` plus an overall pass/fail exit code.

### 6a. Default rule thresholds (config.py, changeable live)

| Rule | Default | Used by |
|---|---|---|
| Amount tolerance | greater of 2% or $50 | PO match / decide |
| PO date window | ±30 days from PO issue date | PO match candidate retrieval |
| Duplicate amount tolerance | within 1% | Duplicate detection |
| Duplicate date window | within 10 days | Duplicate detection |
| Extraction confidence floor | 0.75 | Validate → gate to NEEDS_REVIEW below this |
| Required fields for auto-approve | vendor_name_raw, total_amount, invoice_number, invoice_date | Validate |

## 7. Edge cases (locked)

| # | Scenario | Fixture | Expected behavior |
|---|---|---|---|
| A | Split billing: one PO invoiced across two partial invoices (60% then 40%) | Two generated PDFs referencing the same PO | Invoice 1 auto-approves and decrements PO balance. Invoice 2 is compared against *remaining* balance, not original PO amount, and also auto-approves. A naive 1:1 total-match implementation would wrongly flag invoice 2 as over-tolerance — this is the case that proves it doesn't. |
| B | Tax normalization: invoice total is ~8% over the PO amount, but PO is tax-exclusive and invoice is tax-inclusive | One generated PDF + one PO record with explicit tax treatments | Normalize reconciles both to the same basis before comparing; result is a clean match, not a false `AMOUNT_OVER_TOLERANCE`. |
| C | Near-duplicate: same vendor, same amount, different invoice number, dated a few days apart | Two generated PDFs | Second invoice is not silently approved or silently blocked — `NEEDS_REVIEW` with `DUPLICATE_SUSPECTED` and the specific prior invoice ID cited as evidence. |
| D | Degraded scan: skewed/low-quality scanned image, invoice number unreadable | One generated low-quality image | Extraction confidence collapses below threshold; pipeline returns `NEEDS_REVIEW` with `MISSING_REQUIRED_FIELD` / `LOW_EXTRACTION_CONFIDENCE` naming exactly which field(s) are missing — it does not fabricate a plausible invoice number. |

## 8. UI

Single static SPA (`web/static/index.html` + `app.js`, no framework — keeps it fast to build and to explain):

- **Gate tab** — list of documents `AWAITING_APPROVAL`, each with the triage preview, Approve/Reject buttons.
- **Live Run tab** — pick an in-flight or recent run; stage timeline lights up as SSE events arrive (`stage_started`, `stage_completed`, `stage_failed`), each stage expandable to show its JSON payload.
- **Dashboard tab** — table of all runs: timestamp, vendor, invoice #, amount, decision, reason codes. Filter by decision/vendor. Click a row to open that run's full evidence trail (reuses the Live Run detail view in a completed state).

## 9. Testing strategy

- Unit tests (pytest) per component: normalize, validate, vendor_resolve, po_match, decide — each pure-function-testable on fixed inputs, no I/O.
- Golden eval set (`evals/golden/*.json`) covering happy path + A–D, run via `evals/run_evals.py`, structured JSON output with pass/fail per case — this is the regression safety net while iterating under deadline pressure.
- Rerun CLI doubles as a manual debugging tool: any stage, any saved run, isolated.

## 10. Stack & dependencies

Python, FastAPI, LangGraph, pydantic, `sqlite3` (stdlib), `pdfplumber`, `reportlab` (test PDF generation), `Pillow` (degraded-scan fixture generation), Anthropic SDK (vision + triage calls), `imaplib`/`email` (stdlib, for the IMAP source), rapidfuzz for vendor fuzzy matching (small, well-known — auto-added per your dependency rule). No Docker, no Postgres, no ORM, no frontend framework — all deliberate scope cuts to fit 48 hours without touching what's actually graded.

## 11. Assumptions (flagged per the case study's own FAQ instruction to note assumptions for the live pitch)

- Test data (invoices, vendor master, PO dataset) is generated, not real — per the case study FAQ, this is expected.
- A single throwaway Gmail inbox is used for the IMAP demo; credentials via `.env`, never committed.
- "Approved vendor" is a boolean flag on the vendor master; no external verification.
- Currency is single-currency (USD) for all fixtures — multi-currency conversion is out of scope.
- Duplicate detection window and amount tolerance are illustrative config values, stated explicitly in `pipeline/config.py` and callable out in the demo, not derived from real historical data.

## 12. Build sequence (high-level; hour-by-hour detail in the implementation plan)

1. Scaffold + shared schemas + config + datasets (vendors, POs).
2. Happy path end-to-end: intake (folder) → gate → extract (text PDF) → normalize → validate → vendor resolve → po match → decide, with persisted stage artifacts.
3. Web UI: live run view + dashboard + gate, wired to the happy path via SSE.
4. IMAP intake source, wired in alongside folder/upload.
5. Vision extraction path for scanned PDFs.
6. Edge cases A–D: fixtures + logic + golden evals, one at a time, happy path re-verified after each.
7. Rerun CLI, unit tests, polish, demo script + rehearsal.
