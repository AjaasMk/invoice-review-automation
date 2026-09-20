# Invoice Decision Pipeline

An end-to-end invoice processing pipeline: an invoice arrives (email, folder drop, or upload), a human gates it at intake, then the system extracts, validates, matches it to a purchase order, and produces a reasoned decision — `AUTO_APPROVE`, `NEEDS_REVIEW`, or `REJECT` — with a full evidence trail. Built for the Zamp AI Solutions Associate case study (PS-1).

Design rationale and architecture: [`docs/superpowers/specs/2026-09-20-invoice-decision-pipeline-design.md`](docs/superpowers/specs/2026-09-20-invoice-decision-pipeline-design.md).

## Setup

```bash
python -m pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` — the model reads the invoice; it never decides approve/reject:
- `LLM_PROVIDER` — `anthropic` or `nvidia`. Leave blank and it auto-picks whichever API key is set (NVIDIA first if both are present).
- `ANTHROPIC_API_KEY` — for the Claude-backed clients.
- `NVIDIA_API_KEY` + `NVIDIA_MODEL` — for the free NVIDIA `integrate.api.nvidia.com` endpoint (OpenAI-compatible). `NVIDIA_MODEL` defaults to `moonshotai/kimi-k3`, but that model was hanging indefinitely on non-streaming calls as of this build (confirmed: valid model, working key, other models on the same account respond in under a second) — `meta/llama-3.2-11b-vision-instruct` is the confirmed-working fallback and is what's actually configured right now. Swap `NVIDIA_MODEL` back once kimi-k3 recovers.
- `GMAIL_IMAP_USER` / `GMAIL_IMAP_APP_PASSWORD` — optional. Leave blank to run on folder/upload intake only. If set, needs a Gmail [App Password](https://myaccount.google.com/apppasswords) (requires 2-Step Verification), not the account password.

Generate the test fixtures (happy path + all 4 edge cases) if `data/invoices/` is empty:

```bash
python -m data.generate_fixtures
```

## Run it

```bash
python -m web.server
```

No API key handy? `python preview_server.py` runs the same UI on fake extraction/triage clients (port 8123) so you can check layout and flow without spending a real call.

Open `http://localhost:8000`. Three tabs:
- **Gate** — drag an invoice in (or drop a file into `runs/inbox/`, or send one to the configured Gmail inbox if IMAP is on) and approve/reject it.
- **Live Run** — watch each pipeline stage light up in real time over SSE as it executes.
- **Dashboard** — every run, its decision, and its reason codes; click a row to reopen its full trace.

## Verify it

```bash
python -m pytest tests/ -v          # 77 unit/integration tests
python -m evals.run_evals           # structured JSON pass/fail across happy path + all 4 edge cases
```

`run_evals` exits 0 only if every case matches its expected outcome and reason codes exactly — this is the regression gate, not just the unit tests.

## Rerun any stage in isolation

Every stage's output is persisted per run. To re-evaluate just the matching or decision logic after a rule/config change, without re-running extraction (no repeat API cost):

```bash
python -m pipeline.rerun --run <run_id> --from po_match
python -m pipeline.rerun --run <run_id> --from decide
```

## Architecture, in one paragraph

FastAPI serves the UI and API over SSE. LangGraph orchestrates the pipeline as a checkpointed state machine: triage → **human gate (durable interrupt)** → extract (pdfplumber for machine-readable PDFs, Claude vision for scans/images) → normalize → validate → vendor resolve (fuzzy match) → PO match (candidate retrieval + weighted scoring + cumulative balance tracking + duplicate detection) → decide (pure, config-driven rules). SQLite stores runs, per-stage artifacts, vendors, and POs. The model only ever extracts structured fields — every approve/reject/flag decision is a deterministic function over those fields, so it's reproducible and explainable.

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
- `AnthropicExtractionClient`/`AnthropicTriageClient`/`RealImapClient` are not covered by the automated test suite — they need live credentials. See the manual smoke-test commands in the design spec (§ extraction, § IMAP) to exercise them directly.
- Amount tolerance, duplicate detection window, and confidence floor are illustrative defaults in `pipeline/config.py`, not derived from real historical data — callable out live if asked.
- Real-model testing surfaced something worth knowing going in: a vision model shown a genuinely illegible scan doesn't reliably self-report low confidence — it can fabricate a complete, plausible-looking invoice instead of admitting it can't read one. `EXTRACTION_INSTRUCTIONS` explicitly forbids this now, and `parse_json_response` degrades to an all-null result (rather than crashing) when a model abstains in prose instead of JSON — but this is a real, ongoing model-behavior risk worth stating plainly, not a solved problem.
