# Invoice Decision Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an end-to-end invoice decision pipeline: email/folder/upload intake → human gate → hybrid extraction → normalize/validate → vendor resolve → PO match → deterministic decision, with a live-run + dashboard UI, for the Zamp PS-1 case study.

**Architecture:** FastAPI serves a static SPA over SSE; LangGraph orchestrates the pipeline as a checkpointed state machine with a durable human-in-the-loop interrupt at the gate; SQLite stores runs, stage artifacts, vendors, and POs; every stage is a typed pure function (pydantic in/out) persisted to disk so any stage can be re-run in isolation.

**Tech Stack:** Python 3.11+, FastAPI, LangGraph, pydantic v2, sqlite3 (stdlib), pdfplumber, Anthropic SDK (vision + triage), rapidfuzz, reportlab + Pillow (test fixtures), pytest.

**Spec:** `docs/superpowers/specs/2026-09-20-invoice-decision-pipeline-design.md`

## Global Constraints

- Type hints required on every function/method (per user convention).
- No comments or docstrings anywhere in Python/JS/HTML/CSS source — naming carries the meaning; rationale goes in commit messages only. This applies to every code block below: copy them verbatim, don't add comments while implementing.
- No bare `except: pass` — every caught exception path must say what failed and why (e.g. `raise ValueError(f"...: {e}") from e` or a logged message with the original exception).
- Conventional commits (`feat:`, `fix:`, `test:`, `docs:`, `chore:`).
- Every pipeline stage re-runnable in isolation from saved input (rerun CLI, Task 17).
- Never hardcode API keys/credentials — `.env` (gitignored), loaded via `python-dotenv`.
- Small well-known deps (pydantic, requests, rapidfuzz, python-dotenv) — no approval needed. LangGraph, FastAPI, pdfplumber, reportlab, Pillow, Anthropic SDK are already agreed in the spec.
- Amount tolerance = greater of 2% or $50. PO date window = ±30 days. Duplicate amount tolerance = within 1%. Duplicate date window = within 10 days. Extraction confidence floor = 0.75. Required fields for auto-approve = `vendor_name_raw`, `total_amount`, `invoice_number`, `invoice_date`. (Exact values from spec §6a — implemented as named constants in `pipeline/config.py`, never inlined as magic numbers elsewhere.)

---

### Task 1: Project scaffold, shared schemas, config

**Files:**
- Create: `requirements.txt`
- Create: `.env.example`
- Create: `pipeline/__init__.py`
- Create: `pipeline/schemas.py`
- Create: `pipeline/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: every pydantic model in `pipeline/schemas.py` (`IncomingDocument`, `TriageResult`, `LineItem`, `Invoice`, `VendorRecord`, `PurchaseOrder`, `MatchEvidence`, `MatchResult`, `Decision`) exactly as in spec §5, plus named constants in `pipeline/config.py`: `AMOUNT_TOLERANCE_PCT: Decimal`, `AMOUNT_TOLERANCE_MIN: Decimal`, `PO_DATE_WINDOW_DAYS: int`, `DUPLICATE_AMOUNT_TOLERANCE_PCT: Decimal`, `DUPLICATE_DATE_WINDOW_DAYS: int`, `EXTRACTION_CONFIDENCE_FLOOR: float`, `REQUIRED_FIELDS_FOR_AUTO_APPROVE: list[str]`.

- [ ] **Step 1: Write requirements.txt and .env.example**

`requirements.txt`:
```
fastapi
uvicorn[standard]
langgraph
langgraph-checkpoint-sqlite
pydantic>=2
pdfplumber
reportlab
Pillow
anthropic
rapidfuzz
python-dotenv
pytest
httpx
```

`.env.example`:
```
ANTHROPIC_API_KEY=
GMAIL_IMAP_HOST=imap.gmail.com
GMAIL_IMAP_USER=
GMAIL_IMAP_APP_PASSWORD=
```

- [ ] **Step 2: Install dependencies**

Run: `pip install -r requirements.txt`
Expected: all packages install without error. If `langgraph-checkpoint-sqlite` fails to resolve, run `pip index versions langgraph-checkpoint-sqlite` to find the correct current package name for the installed `langgraph` major version and adjust — the checkpointer package name has moved before across langgraph releases.

- [ ] **Step 3: Write the failing test for config**

```python
from decimal import Decimal

from pipeline import config


def test_amount_tolerance_defaults() -> None:
    assert config.AMOUNT_TOLERANCE_PCT == Decimal("0.02")
    assert config.AMOUNT_TOLERANCE_MIN == Decimal("50")


def test_duplicate_window_defaults() -> None:
    assert config.DUPLICATE_DATE_WINDOW_DAYS == 10
    assert config.DUPLICATE_AMOUNT_TOLERANCE_PCT == Decimal("0.01")


def test_required_fields_defaults() -> None:
    assert config.REQUIRED_FIELDS_FOR_AUTO_APPROVE == [
        "vendor_name_raw",
        "total_amount",
        "invoice_number",
        "invoice_date",
    ]
```

- [ ] **Step 4: Run test to verify it fails**

Run: `pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pipeline'` or `AttributeError`.

- [ ] **Step 5: Write pipeline/__init__.py (empty) and pipeline/schemas.py**

```python
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field


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
    invoice_number: str | None = None
    invoice_date: date | None = None
    vendor_name_raw: str
    po_reference: str | None = None
    line_items: list[LineItem] = Field(default_factory=list)
    subtotal: Decimal | None = None
    tax_amount: Decimal | None = None
    total_amount: Decimal | None = None
    currency: str = "USD"
    extraction_path: Literal["pdf_text", "vision"]
    extraction_confidence: float
    missing_fields: list[str] = Field(default_factory=list)


class VendorRecord(BaseModel):
    vendor_id: str
    canonical_name: str
    aliases: list[str] = Field(default_factory=list)
    approved: bool = True


class PurchaseOrder(BaseModel):
    po_id: str
    vendor_id: str
    amount: Decimal
    currency: str = "USD"
    issued_date: date
    tax_treatment: Literal["inclusive", "exclusive"]
    invoiced_to_date: Decimal = Decimal("0")


class MatchEvidence(BaseModel):
    label: str
    detail: str
    weight: float


class MatchResult(BaseModel):
    po: PurchaseOrder | None = None
    vendor: VendorRecord | None = None
    score: float = 0.0
    evidence: list[MatchEvidence] = Field(default_factory=list)
    comparison_amount: Decimal | None = None
    amount_within_tolerance: bool = False
    remaining_po_balance: Decimal | None = None
    duplicate_of: list[str] = Field(default_factory=list)


class Decision(BaseModel):
    outcome: Literal["AUTO_APPROVE", "NEEDS_REVIEW", "REJECT"]
    reason_codes: list[str] = Field(default_factory=list)
    explanation: str
    evidence: list[MatchEvidence] = Field(default_factory=list)


class RunRecord(BaseModel):
    run_id: str
    doc_id: str
    status: Literal["AWAITING_APPROVAL", "REJECTED_AT_GATE", "RUNNING", "DONE", "ERROR"]
    decision: Decision | None = None
    created_at: datetime
    updated_at: datetime
```

- [ ] **Step 6: Write pipeline/config.py**

```python
from decimal import Decimal

AMOUNT_TOLERANCE_PCT = Decimal("0.02")
AMOUNT_TOLERANCE_MIN = Decimal("50")
PO_DATE_WINDOW_DAYS = 30
DUPLICATE_AMOUNT_TOLERANCE_PCT = Decimal("0.01")
DUPLICATE_DATE_WINDOW_DAYS = 10
EXTRACTION_CONFIDENCE_FLOOR = 0.75
REQUIRED_FIELDS_FOR_AUTO_APPROVE = [
    "vendor_name_raw",
    "total_amount",
    "invoice_number",
    "invoice_date",
]
VENDOR_MATCH_THRESHOLD = 85.0
```

- [ ] **Step 7: Run test to verify it passes**

Run: `pytest tests/test_config.py -v`
Expected: PASS (3 tests).

- [ ] **Step 8: Commit**

```bash
git add requirements.txt .env.example pipeline/__init__.py pipeline/schemas.py pipeline/config.py tests/test_config.py
git commit -m "feat: scaffold project with shared schemas and config"
```

---

### Task 2: Vendor master + PO dataset fixtures

**Files:**
- Create: `data/vendors.json`
- Create: `data/purchase_orders.json`
- Test: `tests/test_fixtures_load.py`

**Interfaces:**
- Produces: on-disk JSON matching `VendorRecord`/`PurchaseOrder` field names exactly, used by Task 8 (storage) to seed the DB and by Task 11 (fixture generation) to keep generated invoice PDFs numerically consistent with these records.

This dataset is deliberately shaped around the four locked edge cases:
- `V-ACME` (approved) owns `PO-1001` (happy path, $5,000, exclusive), `PO-1003` (edge B tax mismatch, $3,000, exclusive), `PO-1004` (edge C duplicate target, $2,000, exclusive), `PO-1005` (edge D degraded scan, $1,500, exclusive).
- `V-GLOBEX` (approved) owns `PO-1002` (edge A split billing, $10,000, exclusive).
- `V-INITECH` (**not** approved) has no POs — exists purely so `decide()`'s `VENDOR_NOT_APPROVED` path has a real fixture to unit-test against, not just a synthetic one.

- [ ] **Step 1: Write data/vendors.json**

```json
[
  {
    "vendor_id": "V-ACME",
    "canonical_name": "Acme Corporation Pvt Ltd",
    "aliases": ["Acme Corp", "ACME CORP.", "Acme Corporation"],
    "approved": true
  },
  {
    "vendor_id": "V-GLOBEX",
    "canonical_name": "Globex Industries LLC",
    "aliases": ["Globex Industries", "Globex"],
    "approved": true
  },
  {
    "vendor_id": "V-INITECH",
    "canonical_name": "Initech Solutions Inc",
    "aliases": ["Initech", "Initech Solutions"],
    "approved": false
  }
]
```

- [ ] **Step 2: Write data/purchase_orders.json**

```json
[
  {
    "po_id": "PO-1001",
    "vendor_id": "V-ACME",
    "amount": "5000.00",
    "currency": "USD",
    "issued_date": "2026-08-01",
    "tax_treatment": "exclusive",
    "invoiced_to_date": "0"
  },
  {
    "po_id": "PO-1002",
    "vendor_id": "V-GLOBEX",
    "amount": "10000.00",
    "currency": "USD",
    "issued_date": "2026-08-05",
    "tax_treatment": "exclusive",
    "invoiced_to_date": "0"
  },
  {
    "po_id": "PO-1003",
    "vendor_id": "V-ACME",
    "amount": "3000.00",
    "currency": "USD",
    "issued_date": "2026-08-10",
    "tax_treatment": "exclusive",
    "invoiced_to_date": "0"
  },
  {
    "po_id": "PO-1004",
    "vendor_id": "V-ACME",
    "amount": "2000.00",
    "currency": "USD",
    "issued_date": "2026-08-12",
    "tax_treatment": "exclusive",
    "invoiced_to_date": "0"
  },
  {
    "po_id": "PO-1005",
    "vendor_id": "V-ACME",
    "amount": "1500.00",
    "currency": "USD",
    "issued_date": "2026-08-15",
    "tax_treatment": "exclusive",
    "invoiced_to_date": "0"
  }
]
```

- [ ] **Step 3: Write the failing test**

```python
import json
from pathlib import Path

from pipeline.schemas import PurchaseOrder, VendorRecord

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def test_vendors_load_and_parse() -> None:
    raw = json.loads((DATA_DIR / "vendors.json").read_text())
    vendors = [VendorRecord.model_validate(v) for v in raw]
    assert len(vendors) == 3
    by_id = {v.vendor_id: v for v in vendors}
    assert by_id["V-ACME"].approved is True
    assert by_id["V-INITECH"].approved is False


def test_purchase_orders_load_and_parse() -> None:
    raw = json.loads((DATA_DIR / "purchase_orders.json").read_text())
    pos = [PurchaseOrder.model_validate(p) for p in raw]
    assert len(pos) == 5
    by_id = {p.po_id: p for p in pos}
    assert by_id["PO-1002"].vendor_id == "V-GLOBEX"
    assert by_id["PO-1001"].tax_treatment == "exclusive"
```

- [ ] **Step 4: Run test to verify it fails**

Run: `pytest tests/test_fixtures_load.py -v`
Expected: FAIL with `FileNotFoundError` (before Steps 1–2) — since this plan lists data files before the test, run the test now to confirm it PASSES instead, and treat that as this task's verification (data-only tasks skip the red step; there is no code to be wrong yet).

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_fixtures_load.py -v`
Expected: PASS (2 tests).

- [ ] **Step 6: Commit**

```bash
git add data/vendors.json data/purchase_orders.json tests/test_fixtures_load.py
git commit -m "feat: add vendor master and PO dataset fixtures"
```
