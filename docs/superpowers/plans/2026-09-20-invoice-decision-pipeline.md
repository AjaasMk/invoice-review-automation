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
git commit -m "feat: vendor and PO fixtures"
```

---

### Task 3: normalize.py

**Files:**
- Create: `pipeline/normalize.py`
- Test: `tests/test_normalize.py`

**Interfaces:**
- Consumes: nothing from earlier tasks except `pipeline.schemas.Invoice`, `LineItem`.
- Produces: `normalize_invoice(raw: dict, extraction_path: Literal["pdf_text", "vision"], confidence: float) -> Invoice`, used by Task 9 (extraction router) and the graph (Task 12).

`raw` is the extraction layer's loosely-typed output: `{"invoice_number": str | None, "invoice_date": str | None (ISO), "vendor_name": str | None, "po_reference": str | None, "line_items": [{"description": str, "quantity": number, "unit_price": number, "line_total": number}], "subtotal": number | None, "tax_amount": number | None, "total_amount": number | None, "currency": str | None}`.

- [ ] **Step 1: Write the failing test**

```python
from decimal import Decimal

from pipeline.normalize import normalize_invoice


def test_normalize_full_invoice() -> None:
    raw = {
        "invoice_number": "INV-9001",
        "invoice_date": "2026-08-02",
        "vendor_name": "Acme Corp",
        "po_reference": "PO-1001",
        "line_items": [
            {"description": "Widgets", "quantity": 10, "unit_price": 500, "line_total": 5000}
        ],
        "subtotal": 5000,
        "tax_amount": 0,
        "total_amount": 5000,
        "currency": "USD",
    }
    invoice = normalize_invoice(raw, "pdf_text", 0.95)
    assert invoice.invoice_number == "INV-9001"
    assert invoice.invoice_date.isoformat() == "2026-08-02"
    assert invoice.vendor_name_raw == "Acme Corp"
    assert invoice.po_reference == "PO-1001"
    assert invoice.total_amount == Decimal("5000")
    assert invoice.line_items[0].line_total == Decimal("5000")
    assert invoice.extraction_path == "pdf_text"
    assert invoice.extraction_confidence == 0.95


def test_normalize_missing_fields_left_none() -> None:
    raw = {"vendor_name": "Globex", "line_items": []}
    invoice = normalize_invoice(raw, "vision", 0.4)
    assert invoice.invoice_number is None
    assert invoice.invoice_date is None
    assert invoice.total_amount is None
    assert invoice.vendor_name_raw == "Globex"


def test_normalize_rejects_unparsable_date() -> None:
    raw = {"vendor_name": "Acme", "invoice_date": "not-a-date", "line_items": []}
    try:
        normalize_invoice(raw, "pdf_text", 0.9)
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "invoice_date" in str(exc)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_normalize.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pipeline.normalize'`.

- [ ] **Step 3: Write pipeline/normalize.py**

```python
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from pipeline.schemas import Invoice, LineItem


def normalize_invoice(raw: dict[str, Any], extraction_path: str, confidence: float) -> Invoice:
    line_items = [_to_line_item(item) for item in raw.get("line_items", [])]
    return Invoice(
        invoice_number=raw.get("invoice_number"),
        invoice_date=_to_date(raw.get("invoice_date"), "invoice_date"),
        vendor_name_raw=raw.get("vendor_name") or "",
        po_reference=raw.get("po_reference"),
        line_items=line_items,
        subtotal=_to_decimal(raw.get("subtotal"), "subtotal"),
        tax_amount=_to_decimal(raw.get("tax_amount"), "tax_amount"),
        total_amount=_to_decimal(raw.get("total_amount"), "total_amount"),
        currency=raw.get("currency") or "USD",
        extraction_path=extraction_path,
        extraction_confidence=confidence,
    )


def _to_line_item(item: dict[str, Any]) -> LineItem:
    return LineItem(
        description=item.get("description", ""),
        quantity=_to_decimal(item.get("quantity"), "line_item.quantity") or Decimal("1"),
        unit_price=_to_decimal(item.get("unit_price"), "line_item.unit_price") or Decimal("0"),
        line_total=_to_decimal(item.get("line_total"), "line_item.line_total") or Decimal("0"),
    )


def _to_decimal(value: Any, field_name: str) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"could not parse {field_name} as a decimal: {value!r}") from exc


def _to_date(value: Any, field_name: str) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise ValueError(f"could not parse {field_name} as an ISO date: {value!r}") from exc
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_normalize.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add pipeline/normalize.py tests/test_normalize.py
git commit -m "feat: invoice normalization"
```

---

### Task 4: validate.py

**Files:**
- Create: `pipeline/validate.py`
- Test: `tests/test_validate.py`

**Interfaces:**
- Consumes: `pipeline.schemas.Invoice`, `pipeline.config.REQUIRED_FIELDS_FOR_AUTO_APPROVE`.
- Produces: `validate_invoice(invoice: Invoice) -> Invoice` (returns a copy with `missing_fields` populated), used by the graph (Task 12) right after normalize.

- [ ] **Step 1: Write the failing test**

```python
from datetime import date
from decimal import Decimal

from pipeline.schemas import Invoice, LineItem
from pipeline.validate import validate_invoice


def _base_invoice(**overrides: object) -> Invoice:
    defaults: dict[str, object] = dict(
        invoice_number="INV-1",
        invoice_date=date(2026, 8, 2),
        vendor_name_raw="Acme Corp",
        line_items=[LineItem(description="Widgets", quantity=Decimal("10"), unit_price=Decimal("500"), line_total=Decimal("5000"))],
        subtotal=Decimal("5000"),
        tax_amount=Decimal("0"),
        total_amount=Decimal("5000"),
        extraction_path="pdf_text",
        extraction_confidence=0.95,
    )
    defaults.update(overrides)
    return Invoice(**defaults)


def test_complete_invoice_has_no_missing_fields() -> None:
    result = validate_invoice(_base_invoice())
    assert result.missing_fields == []


def test_missing_invoice_number_flagged() -> None:
    result = validate_invoice(_base_invoice(invoice_number=None))
    assert "invoice_number" in result.missing_fields


def test_missing_total_flagged() -> None:
    result = validate_invoice(_base_invoice(total_amount=None))
    assert "total_amount" in result.missing_fields


def test_line_items_not_summing_to_subtotal_flagged() -> None:
    result = validate_invoice(_base_invoice(subtotal=Decimal("4000")))
    assert "subtotal_mismatch" in result.missing_fields


def test_subtotal_plus_tax_not_equal_total_flagged() -> None:
    result = validate_invoice(_base_invoice(tax_amount=Decimal("100"), total_amount=Decimal("5000")))
    assert "total_mismatch" in result.missing_fields
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_validate.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pipeline.validate'`.

- [ ] **Step 3: Write pipeline/validate.py**

```python
from decimal import Decimal

from pipeline.config import REQUIRED_FIELDS_FOR_AUTO_APPROVE
from pipeline.schemas import Invoice

ARITHMETIC_TOLERANCE = Decimal("0.01")


def validate_invoice(invoice: Invoice) -> Invoice:
    missing: list[str] = []
    for field_name in REQUIRED_FIELDS_FOR_AUTO_APPROVE:
        value = getattr(invoice, field_name)
        if value in (None, ""):
            missing.append(field_name)

    if invoice.line_items and invoice.subtotal is not None:
        computed_subtotal = sum((item.line_total for item in invoice.line_items), Decimal("0"))
        if abs(computed_subtotal - invoice.subtotal) > ARITHMETIC_TOLERANCE:
            missing.append("subtotal_mismatch")

    if invoice.subtotal is not None and invoice.tax_amount is not None and invoice.total_amount is not None:
        computed_total = invoice.subtotal + invoice.tax_amount
        if abs(computed_total - invoice.total_amount) > ARITHMETIC_TOLERANCE:
            missing.append("total_mismatch")

    return invoice.model_copy(update={"missing_fields": missing})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_validate.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add pipeline/validate.py tests/test_validate.py
git commit -m "feat: invoice validation"
```

---

### Task 5: vendor_resolve.py

**Files:**
- Create: `pipeline/vendor_resolve.py`
- Test: `tests/test_vendor_resolve.py`

**Interfaces:**
- Consumes: `pipeline.schemas.VendorRecord`, `pipeline.config.VENDOR_MATCH_THRESHOLD`, `rapidfuzz.fuzz`.
- Produces: `resolve_vendor(vendor_name_raw: str, vendors: list[VendorRecord]) -> VendorRecord | None`, used by the graph (Task 12) and directly by `po_match` tests (Task 6) as fixture data, not as a call — `po_match` takes an already-resolved `vendor` argument.

- [ ] **Step 1: Write the failing test**

```python
from pipeline.schemas import VendorRecord
from pipeline.vendor_resolve import resolve_vendor

VENDORS = [
    VendorRecord(vendor_id="V-ACME", canonical_name="Acme Corporation Pvt Ltd", aliases=["Acme Corp", "ACME CORP.", "Acme Corporation"], approved=True),
    VendorRecord(vendor_id="V-GLOBEX", canonical_name="Globex Industries LLC", aliases=["Globex Industries", "Globex"], approved=True),
]


def test_resolves_exact_canonical_name() -> None:
    result = resolve_vendor("Globex Industries LLC", VENDORS)
    assert result is not None
    assert result.vendor_id == "V-GLOBEX"


def test_resolves_alias_with_different_casing_and_punctuation() -> None:
    result = resolve_vendor("acme corp.", VENDORS)
    assert result is not None
    assert result.vendor_id == "V-ACME"


def test_resolves_close_fuzzy_variant() -> None:
    result = resolve_vendor("Acme Corporation Pvt. Ltd", VENDORS)
    assert result is not None
    assert result.vendor_id == "V-ACME"


def test_returns_none_for_unrelated_name() -> None:
    result = resolve_vendor("Totally Unrelated Vendor Co", VENDORS)
    assert result is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_vendor_resolve.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pipeline.vendor_resolve'`.

- [ ] **Step 3: Write pipeline/vendor_resolve.py**

```python
from rapidfuzz import fuzz

from pipeline.config import VENDOR_MATCH_THRESHOLD
from pipeline.schemas import VendorRecord


def resolve_vendor(vendor_name_raw: str, vendors: list[VendorRecord]) -> VendorRecord | None:
    normalized_input = _normalize_name(vendor_name_raw)
    best_vendor: VendorRecord | None = None
    best_score = 0.0
    for vendor in vendors:
        for candidate in [vendor.canonical_name, *vendor.aliases]:
            score = fuzz.token_sort_ratio(normalized_input, _normalize_name(candidate))
            if score > best_score:
                best_score = score
                best_vendor = vendor
    if best_score >= VENDOR_MATCH_THRESHOLD:
        return best_vendor
    return None


def _normalize_name(name: str) -> str:
    return " ".join(name.lower().replace(",", "").replace(".", "").split())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_vendor_resolve.py -v`
Expected: PASS (4 tests). If `test_resolves_close_fuzzy_variant` fails because the score lands just under 85, lower `VENDOR_MATCH_THRESHOLD` in `pipeline/config.py` to 80 and re-run — token_sort_ratio on this exact pair should land in the high 80s/90s, but rapidfuzz versions can differ slightly; don't hardcode a fudge into the test.

- [ ] **Step 5: Commit**

```bash
git add pipeline/vendor_resolve.py tests/test_vendor_resolve.py
git commit -m "feat: fuzzy vendor resolution"
```

---

### Task 6: po_match.py — this is the core of the grade; covers edge cases A, B, C

**Files:**
- Create: `pipeline/po_match.py`
- Test: `tests/test_po_match.py`

**Interfaces:**
- Consumes: `pipeline.schemas.{Invoice, VendorRecord, PurchaseOrder, MatchResult, MatchEvidence}`, `pipeline.config.{AMOUNT_TOLERANCE_PCT, AMOUNT_TOLERANCE_MIN, PO_DATE_WINDOW_DAYS, DUPLICATE_AMOUNT_TOLERANCE_PCT, DUPLICATE_DATE_WINDOW_DAYS}`.
- Produces: `match_po(invoice: Invoice, vendor: VendorRecord | None, candidate_pos: list[PurchaseOrder], prior_invoices: list[Invoice]) -> MatchResult`, used by the graph (Task 12). **Contract:** `prior_invoices` must already be filtered to the same resolved vendor by the caller (the storage layer, Task 8, filters by `vendor_id`) — `match_po` does not re-check vendor identity on `prior_invoices`, only amount/date/number similarity.

- [ ] **Step 1: Write the failing test**

```python
from datetime import date
from decimal import Decimal

from pipeline.schemas import Invoice, LineItem, MatchResult, PurchaseOrder, VendorRecord
from pipeline.po_match import match_po

ACME = VendorRecord(vendor_id="V-ACME", canonical_name="Acme Corporation Pvt Ltd", aliases=[], approved=True)
GLOBEX = VendorRecord(vendor_id="V-GLOBEX", canonical_name="Globex Industries LLC", aliases=[], approved=True)


def _invoice(**overrides: object) -> Invoice:
    defaults: dict[str, object] = dict(
        invoice_number="INV-1",
        invoice_date=date(2026, 8, 2),
        vendor_name_raw="Acme Corp",
        po_reference=None,
        line_items=[],
        subtotal=Decimal("5000"),
        tax_amount=Decimal("0"),
        total_amount=Decimal("5000"),
        extraction_path="pdf_text",
        extraction_confidence=0.95,
    )
    defaults.update(overrides)
    return Invoice(**defaults)


def test_happy_path_auto_matches_full_amount() -> None:
    po = PurchaseOrder(po_id="PO-1001", vendor_id="V-ACME", amount=Decimal("5000"), issued_date=date(2026, 8, 1), tax_treatment="exclusive")
    result = match_po(_invoice(po_reference="PO-1001"), ACME, [po], [])
    assert result.po is not None and result.po.po_id == "PO-1001"
    assert result.amount_within_tolerance is True
    assert result.remaining_po_balance == Decimal("0")


def test_split_billing_second_invoice_compares_to_remaining_balance() -> None:
    po = PurchaseOrder(po_id="PO-1002", vendor_id="V-GLOBEX", amount=Decimal("10000"), issued_date=date(2026, 8, 5), tax_treatment="exclusive", invoiced_to_date=Decimal("6000"))
    invoice = _invoice(vendor_name_raw="Globex", po_reference="PO-1002", subtotal=Decimal("4000"), total_amount=Decimal("4000"))
    result = match_po(invoice, GLOBEX, [po], [])
    assert result.amount_within_tolerance is True
    assert result.remaining_po_balance == Decimal("0")


def test_tax_exclusive_po_compares_against_subtotal_not_total() -> None:
    po = PurchaseOrder(po_id="PO-1003", vendor_id="V-ACME", amount=Decimal("3000"), issued_date=date(2026, 8, 10), tax_treatment="exclusive")
    invoice = _invoice(po_reference="PO-1003", subtotal=Decimal("3000"), tax_amount=Decimal("240"), total_amount=Decimal("3240"))
    result = match_po(invoice, ACME, [po], [])
    assert result.comparison_amount == Decimal("3000")
    assert result.amount_within_tolerance is True


def test_tax_inclusive_po_compares_against_total() -> None:
    po = PurchaseOrder(po_id="PO-9999", vendor_id="V-ACME", amount=Decimal("3240"), issued_date=date(2026, 8, 10), tax_treatment="inclusive")
    invoice = _invoice(po_reference="PO-9999", subtotal=Decimal("3000"), tax_amount=Decimal("240"), total_amount=Decimal("3240"))
    result = match_po(invoice, ACME, [po], [])
    assert result.comparison_amount == Decimal("3240")
    assert result.amount_within_tolerance is True


def test_no_vendor_returns_no_match() -> None:
    result = match_po(_invoice(), None, [], [])
    assert result.vendor is None
    assert result.po is None


def test_no_po_for_vendor_returns_po_none() -> None:
    result = match_po(_invoice(), ACME, [], [])
    assert result.vendor is not None
    assert result.po is None


def test_duplicate_detection_flags_near_duplicate_within_window() -> None:
    prior = _invoice(invoice_number="INV-2001", invoice_date=date(2026, 8, 1), total_amount=Decimal("2000"))
    current = _invoice(invoice_number="INV-2002", invoice_date=date(2026, 8, 4), total_amount=Decimal("1995"))
    result = match_po(current, ACME, [], [prior])
    assert "INV-2001" in result.duplicate_of


def test_duplicate_detection_ignores_same_invoice_number() -> None:
    prior = _invoice(invoice_number="INV-1", invoice_date=date(2026, 8, 1), total_amount=Decimal("2000"))
    current = _invoice(invoice_number="INV-1", invoice_date=date(2026, 8, 4), total_amount=Decimal("2000"))
    result = match_po(current, ACME, [], [prior])
    assert result.duplicate_of == []


def test_explicit_po_reference_wins_over_closer_amount_match() -> None:
    referenced = PurchaseOrder(po_id="PO-A", vendor_id="V-ACME", amount=Decimal("5000"), issued_date=date(2026, 8, 1), tax_treatment="exclusive")
    closer_amount = PurchaseOrder(po_id="PO-B", vendor_id="V-ACME", amount=Decimal("4999"), issued_date=date(2026, 8, 1), tax_treatment="exclusive")
    invoice = _invoice(po_reference="PO-A", subtotal=Decimal("4999"), total_amount=Decimal("4999"))
    result = match_po(invoice, ACME, [referenced, closer_amount], [])
    assert result.po is not None and result.po.po_id == "PO-A"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_po_match.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pipeline.po_match'`.

- [ ] **Step 3: Write pipeline/po_match.py**

```python
from decimal import Decimal

from pipeline.config import (
    AMOUNT_TOLERANCE_MIN,
    AMOUNT_TOLERANCE_PCT,
    DUPLICATE_AMOUNT_TOLERANCE_PCT,
    DUPLICATE_DATE_WINDOW_DAYS,
    PO_DATE_WINDOW_DAYS,
)
from pipeline.schemas import Invoice, MatchEvidence, MatchResult, PurchaseOrder, VendorRecord


def match_po(
    invoice: Invoice,
    vendor: VendorRecord | None,
    candidate_pos: list[PurchaseOrder],
    prior_invoices: list[Invoice],
) -> MatchResult:
    duplicate_of = _find_duplicates(invoice, prior_invoices)

    if vendor is None:
        return MatchResult(
            vendor=None,
            evidence=[MatchEvidence(label="vendor", detail=f"No vendor match for '{invoice.vendor_name_raw}'", weight=0.0)],
            duplicate_of=duplicate_of,
        )

    vendor_pos = [po for po in candidate_pos if po.vendor_id == vendor.vendor_id]
    if not vendor_pos:
        return MatchResult(
            vendor=vendor,
            evidence=[MatchEvidence(label="po_search", detail=f"No purchase orders found for vendor {vendor.canonical_name}", weight=0.0)],
            duplicate_of=duplicate_of,
        )

    scored = [(po, *_score_po(invoice, po)) for po in vendor_pos]
    best_po, best_score, best_evidence = max(scored, key=lambda item: item[1])

    comparison_amount = _comparison_amount(invoice, best_po)
    remaining_before = best_po.amount - best_po.invoiced_to_date
    tolerance = max(best_po.amount * AMOUNT_TOLERANCE_PCT, AMOUNT_TOLERANCE_MIN)
    within_tolerance = comparison_amount is not None and comparison_amount <= remaining_before + tolerance
    remaining_after = remaining_before - comparison_amount if comparison_amount is not None else remaining_before

    return MatchResult(
        po=best_po,
        vendor=vendor,
        score=best_score,
        evidence=best_evidence,
        comparison_amount=comparison_amount,
        amount_within_tolerance=within_tolerance,
        remaining_po_balance=remaining_after,
        duplicate_of=duplicate_of,
    )


def _comparison_amount(invoice: Invoice, po: PurchaseOrder) -> Decimal | None:
    if po.tax_treatment == "inclusive":
        return invoice.total_amount
    return invoice.subtotal if invoice.subtotal is not None else invoice.total_amount


def _score_po(invoice: Invoice, po: PurchaseOrder) -> tuple[float, list[MatchEvidence]]:
    evidence: list[MatchEvidence] = []
    score = 0.0

    if invoice.po_reference and invoice.po_reference.strip().lower() == po.po_id.lower():
        weight = 0.6
        score += weight
        evidence.append(MatchEvidence(label="po_reference", detail=f"Invoice explicitly references {po.po_id}", weight=weight))

    if invoice.invoice_date is not None:
        days = abs((invoice.invoice_date - po.issued_date).days)
        if days <= PO_DATE_WINDOW_DAYS:
            weight = 0.2 * (1 - (days / PO_DATE_WINDOW_DAYS))
            score += weight
            evidence.append(MatchEvidence(label="date_proximity", detail=f"Invoice dated {days} day(s) from PO issue date", weight=weight))

    remaining = po.amount - po.invoiced_to_date
    comparison = _comparison_amount(invoice, po)
    if comparison is not None and remaining > 0:
        closeness = max(Decimal("0"), Decimal("1") - abs(comparison - remaining) / remaining)
        weight = float(closeness) * 0.2
        score += weight
        evidence.append(MatchEvidence(label="amount_proximity", detail=f"Comparison amount {comparison} close to remaining PO balance {remaining}", weight=weight))

    return score, evidence


def _find_duplicates(invoice: Invoice, prior_invoices: list[Invoice]) -> list[str]:
    duplicates: list[str] = []
    if invoice.total_amount is None or invoice.invoice_date is None:
        return duplicates
    for prior in prior_invoices:
        if prior.invoice_number == invoice.invoice_number:
            continue
        if prior.total_amount is None or prior.invoice_date is None or prior.total_amount == 0:
            continue
        amount_diff_pct = abs(invoice.total_amount - prior.total_amount) / prior.total_amount
        if amount_diff_pct > DUPLICATE_AMOUNT_TOLERANCE_PCT:
            continue
        if abs((invoice.invoice_date - prior.invoice_date).days) > DUPLICATE_DATE_WINDOW_DAYS:
            continue
        duplicates.append(prior.invoice_number or "unknown")
    return duplicates
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_po_match.py -v`
Expected: PASS (9 tests).

- [ ] **Step 5: Commit**

```bash
git add pipeline/po_match.py tests/test_po_match.py
git commit -m "feat: PO matching with split-billing, tax basis, and duplicate detection"
```

---

### Task 7: decision.py — covers edge case D (low-confidence refusal) and the full reason-code matrix

**Files:**
- Create: `pipeline/decision.py`
- Test: `tests/test_decision.py`

**Interfaces:**
- Consumes: `pipeline.schemas.{Invoice, MatchResult, Decision}`, `pipeline.config.EXTRACTION_CONFIDENCE_FLOOR`.
- Produces: `decide(invoice: Invoice, match: MatchResult) -> Decision`, used by the graph (Task 12) as the final node and directly by the golden eval runner (Task 18).

- [ ] **Step 1: Write the failing test**

```python
from datetime import date
from decimal import Decimal

from pipeline.decision import decide
from pipeline.schemas import Invoice, MatchResult, PurchaseOrder, VendorRecord

ACME = VendorRecord(vendor_id="V-ACME", canonical_name="Acme Corporation Pvt Ltd", aliases=[], approved=True)
INITECH = VendorRecord(vendor_id="V-INITECH", canonical_name="Initech Solutions Inc", aliases=[], approved=False)


def _invoice(**overrides: object) -> Invoice:
    defaults: dict[str, object] = dict(
        invoice_number="INV-1",
        invoice_date=date(2026, 8, 2),
        vendor_name_raw="Acme Corp",
        line_items=[],
        subtotal=Decimal("5000"),
        total_amount=Decimal("5000"),
        extraction_path="pdf_text",
        extraction_confidence=0.95,
        missing_fields=[],
    )
    defaults.update(overrides)
    return Invoice(**defaults)


def test_clean_match_auto_approves() -> None:
    po = PurchaseOrder(po_id="PO-1001", vendor_id="V-ACME", amount=Decimal("5000"), issued_date=date(2026, 8, 1), tax_treatment="exclusive")
    match = MatchResult(po=po, vendor=ACME, comparison_amount=Decimal("5000"), amount_within_tolerance=True, remaining_po_balance=Decimal("0"))
    result = decide(_invoice(), match)
    assert result.outcome == "AUTO_APPROVE"
    assert result.reason_codes == []


def test_low_confidence_needs_review_before_matching_is_considered() -> None:
    match = MatchResult()
    result = decide(_invoice(extraction_confidence=0.3), match)
    assert result.outcome == "NEEDS_REVIEW"
    assert "LOW_EXTRACTION_CONFIDENCE" in result.reason_codes


def test_missing_fields_needs_review() -> None:
    match = MatchResult()
    result = decide(_invoice(missing_fields=["invoice_number"]), match)
    assert result.outcome == "NEEDS_REVIEW"
    assert "MISSING_REQUIRED_FIELD" in result.reason_codes


def test_vendor_not_found_needs_review() -> None:
    match = MatchResult(vendor=None, po=None)
    result = decide(_invoice(), match)
    assert result.outcome == "NEEDS_REVIEW"
    assert "VENDOR_NOT_FOUND" in result.reason_codes


def test_vendor_not_approved_rejects_and_short_circuits() -> None:
    match = MatchResult(vendor=INITECH, po=None, duplicate_of=["INV-999"])
    result = decide(_invoice(), match)
    assert result.outcome == "REJECT"
    assert result.reason_codes == ["VENDOR_NOT_APPROVED"]


def test_duplicate_suspected_needs_review() -> None:
    po = PurchaseOrder(po_id="PO-1004", vendor_id="V-ACME", amount=Decimal("5000"), issued_date=date(2026, 8, 1), tax_treatment="exclusive")
    match = MatchResult(po=po, vendor=ACME, comparison_amount=Decimal("5000"), amount_within_tolerance=True, duplicate_of=["INV-2001"])
    result = decide(_invoice(), match)
    assert result.outcome == "NEEDS_REVIEW"
    assert "DUPLICATE_SUSPECTED" in result.reason_codes


def test_po_not_found_needs_review() -> None:
    match = MatchResult(vendor=ACME, po=None)
    result = decide(_invoice(), match)
    assert result.outcome == "NEEDS_REVIEW"
    assert "PO_NOT_FOUND" in result.reason_codes


def test_amount_over_tolerance_when_nothing_invoiced_yet() -> None:
    po = PurchaseOrder(po_id="PO-1001", vendor_id="V-ACME", amount=Decimal("5000"), issued_date=date(2026, 8, 1), tax_treatment="exclusive", invoiced_to_date=Decimal("0"))
    match = MatchResult(po=po, vendor=ACME, comparison_amount=Decimal("6000"), amount_within_tolerance=False)
    result = decide(_invoice(), match)
    assert result.outcome == "NEEDS_REVIEW"
    assert "AMOUNT_OVER_TOLERANCE" in result.reason_codes
    assert "PO_BALANCE_EXCEEDED" not in result.reason_codes


def test_po_balance_exceeded_when_partially_invoiced_already() -> None:
    po = PurchaseOrder(po_id="PO-1002", vendor_id="V-ACME", amount=Decimal("10000"), issued_date=date(2026, 8, 1), tax_treatment="exclusive", invoiced_to_date=Decimal("6000"))
    match = MatchResult(po=po, vendor=ACME, comparison_amount=Decimal("5000"), amount_within_tolerance=False)
    result = decide(_invoice(), match)
    assert result.outcome == "NEEDS_REVIEW"
    assert "PO_BALANCE_EXCEEDED" in result.reason_codes
    assert "AMOUNT_OVER_TOLERANCE" not in result.reason_codes


def test_reason_codes_accumulate_when_multiple_apply() -> None:
    match = MatchResult(vendor=None, po=None)
    result = decide(_invoice(extraction_confidence=0.2), match)
    assert set(result.reason_codes) == {"LOW_EXTRACTION_CONFIDENCE", "VENDOR_NOT_FOUND"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_decision.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pipeline.decision'`.

- [ ] **Step 3: Write pipeline/decision.py**

```python
from pipeline.config import EXTRACTION_CONFIDENCE_FLOOR
from pipeline.schemas import Decision, Invoice, MatchResult

_EXPLANATIONS = {
    "LOW_EXTRACTION_CONFIDENCE": "Extraction confidence is below the required floor.",
    "MISSING_REQUIRED_FIELD": "One or more required fields could not be extracted.",
    "VENDOR_NOT_FOUND": "No vendor match was found for the invoice's stated vendor name.",
    "VENDOR_NOT_APPROVED": "The matched vendor is not on the approved vendor list.",
    "DUPLICATE_SUSPECTED": "This invoice closely resembles a prior invoice from the same vendor.",
    "PO_NOT_FOUND": "No matching purchase order was found for this vendor.",
    "AMOUNT_OVER_TOLERANCE": "The invoice amount is outside the allowed tolerance of the purchase order amount.",
    "PO_BALANCE_EXCEEDED": "The invoice amount exceeds the purchase order's remaining balance beyond tolerance.",
}


def decide(invoice: Invoice, match: MatchResult) -> Decision:
    reason_codes: list[str] = []

    if invoice.extraction_confidence < EXTRACTION_CONFIDENCE_FLOOR:
        reason_codes.append("LOW_EXTRACTION_CONFIDENCE")
    if invoice.missing_fields:
        reason_codes.append("MISSING_REQUIRED_FIELD")

    if match.vendor is None:
        reason_codes.append("VENDOR_NOT_FOUND")
    elif not match.vendor.approved:
        return Decision(
            outcome="REJECT",
            reason_codes=["VENDOR_NOT_APPROVED"],
            explanation=_EXPLANATIONS["VENDOR_NOT_APPROVED"],
            evidence=match.evidence,
        )

    if match.duplicate_of:
        reason_codes.append("DUPLICATE_SUSPECTED")

    if match.vendor is not None and match.po is None:
        reason_codes.append("PO_NOT_FOUND")

    if match.po is not None and match.comparison_amount is not None and not match.amount_within_tolerance:
        if match.po.invoiced_to_date > 0:
            reason_codes.append("PO_BALANCE_EXCEEDED")
        else:
            reason_codes.append("AMOUNT_OVER_TOLERANCE")

    if not reason_codes:
        vendor_name = match.vendor.canonical_name if match.vendor else invoice.vendor_name_raw
        po_id = match.po.po_id if match.po else "unknown"
        return Decision(
            outcome="AUTO_APPROVE",
            reason_codes=[],
            explanation=f"Invoice {invoice.invoice_number} from {vendor_name} matches {po_id} within tolerance.",
            evidence=match.evidence,
        )

    explanation = " ".join(_EXPLANATIONS[code] for code in reason_codes)
    return Decision(outcome="NEEDS_REVIEW", reason_codes=reason_codes, explanation=explanation, evidence=match.evidence)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_decision.py -v`
Expected: PASS (10 tests).

- [ ] **Step 5: Commit**

```bash
git add pipeline/decision.py tests/test_decision.py
git commit -m "feat: deterministic decision rules"
```

---

### Task 8: storage.py — SQLite repository

**Files:**
- Create: `pipeline/storage.py`
- Test: `tests/test_storage.py`

**Interfaces:**
- Consumes: `pipeline.schemas.{VendorRecord, PurchaseOrder, Invoice, Decision}`.
- Produces class `Repository` with methods used by the graph (Task 12), web server (Task 15), and rerun CLI (Task 17): `__init__(self, db_path: str)`, `init_db(self) -> None`, `seed_reference_data(self, vendors: list[VendorRecord], purchase_orders: list[PurchaseOrder]) -> None`, `get_vendors(self) -> list[VendorRecord]`, `get_purchase_orders(self) -> list[PurchaseOrder]`, `update_po_balance(self, po_id: str, new_invoiced_to_date: Decimal) -> None`, `create_run(self, run_id: str, doc_id: str, status: str) -> None`, `update_run_status(self, run_id: str, status: str) -> None`, `set_run_decision(self, run_id: str, decision: Decision) -> None`, `append_stage(self, run_id: str, stage: str, payload: dict) -> int`, `get_stages(self, run_id: str) -> list[dict]`, `list_runs(self) -> list[dict]`, `get_run(self, run_id: str) -> dict | None`, `save_invoice_record(self, run_id: str, vendor_id: str, invoice: Invoice) -> None`, `get_prior_invoices(self, vendor_id: str, exclude_run_id: str) -> list[Invoice]`.

- [ ] **Step 1: Write the failing test**

```python
from decimal import Decimal
from datetime import date

from pipeline.schemas import Decision, Invoice, PurchaseOrder, VendorRecord
from pipeline.storage import Repository


def _repo(tmp_path) -> Repository:
    repo = Repository(str(tmp_path / "test.db"))
    repo.init_db()
    return repo


def test_seed_and_read_back_vendors_and_pos(tmp_path) -> None:
    repo = _repo(tmp_path)
    vendor = VendorRecord(vendor_id="V-ACME", canonical_name="Acme Corporation Pvt Ltd", aliases=["Acme Corp"], approved=True)
    po = PurchaseOrder(po_id="PO-1001", vendor_id="V-ACME", amount=Decimal("5000"), issued_date=date(2026, 8, 1), tax_treatment="exclusive")
    repo.seed_reference_data([vendor], [po])
    vendors = repo.get_vendors()
    pos = repo.get_purchase_orders()
    assert vendors[0].vendor_id == "V-ACME"
    assert vendors[0].aliases == ["Acme Corp"]
    assert pos[0].amount == Decimal("5000")


def test_update_po_balance_persists(tmp_path) -> None:
    repo = _repo(tmp_path)
    po = PurchaseOrder(po_id="PO-1002", vendor_id="V-GLOBEX", amount=Decimal("10000"), issued_date=date(2026, 8, 5), tax_treatment="exclusive")
    repo.seed_reference_data([], [po])
    repo.update_po_balance("PO-1002", Decimal("6000"))
    pos = repo.get_purchase_orders()
    assert pos[0].invoiced_to_date == Decimal("6000")


def test_run_lifecycle_and_decision(tmp_path) -> None:
    repo = _repo(tmp_path)
    repo.create_run("run-1", "doc-1", "RUNNING")
    repo.update_run_status("run-1", "DONE")
    decision = Decision(outcome="AUTO_APPROVE", reason_codes=[], explanation="ok", evidence=[])
    repo.set_run_decision("run-1", decision)
    run = repo.get_run("run-1")
    assert run is not None
    assert run["status"] == "DONE"
    assert run["decision"]["outcome"] == "AUTO_APPROVE"


def test_stages_persist_in_order(tmp_path) -> None:
    repo = _repo(tmp_path)
    repo.create_run("run-2", "doc-2", "RUNNING")
    repo.append_stage("run-2", "triage", {"looks_like_invoice": True})
    repo.append_stage("run-2", "extract", {"invoice_number": "INV-1"})
    stages = repo.get_stages("run-2")
    assert [s["stage"] for s in stages] == ["triage", "extract"]
    assert stages[1]["payload"]["invoice_number"] == "INV-1"


def test_list_runs_returns_all_runs(tmp_path) -> None:
    repo = _repo(tmp_path)
    repo.create_run("run-a", "doc-a", "RUNNING")
    repo.create_run("run-b", "doc-b", "RUNNING")
    runs = repo.list_runs()
    assert {r["run_id"] for r in runs} == {"run-a", "run-b"}


def test_prior_invoices_filtered_by_vendor_and_excludes_current_run(tmp_path) -> None:
    repo = _repo(tmp_path)
    invoice = Invoice(
        invoice_number="INV-2001",
        invoice_date=date(2026, 8, 1),
        vendor_name_raw="Acme Corp",
        total_amount=Decimal("2000"),
        extraction_path="pdf_text",
        extraction_confidence=0.9,
    )
    repo.save_invoice_record("run-x", "V-ACME", invoice)
    repo.save_invoice_record("run-y", "V-GLOBEX", invoice)
    prior = repo.get_prior_invoices("V-ACME", exclude_run_id="run-z")
    assert len(prior) == 1
    assert prior[0].invoice_number == "INV-2001"
    prior_excluded = repo.get_prior_invoices("V-ACME", exclude_run_id="run-x")
    assert prior_excluded == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_storage.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pipeline.storage'`.

- [ ] **Step 3: Write pipeline/storage.py**

```python
import json
import sqlite3
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from typing import Iterator

from pipeline.schemas import Decision, Invoice, PurchaseOrder, VendorRecord

SCHEMA = """
CREATE TABLE IF NOT EXISTS vendors (
    vendor_id TEXT PRIMARY KEY,
    canonical_name TEXT NOT NULL,
    aliases_json TEXT NOT NULL,
    approved INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS purchase_orders (
    po_id TEXT PRIMARY KEY,
    vendor_id TEXT NOT NULL,
    amount TEXT NOT NULL,
    currency TEXT NOT NULL,
    issued_date TEXT NOT NULL,
    tax_treatment TEXT NOT NULL,
    invoiced_to_date TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    doc_id TEXT NOT NULL,
    status TEXT NOT NULL,
    decision_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS run_stages (
    run_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    stage TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (run_id, seq)
);
CREATE TABLE IF NOT EXISTS invoice_records (
    run_id TEXT PRIMARY KEY,
    vendor_id TEXT NOT NULL,
    invoice_number TEXT,
    invoice_date TEXT,
    total_amount TEXT,
    created_at TEXT NOT NULL
);
"""


class Repository:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    def seed_reference_data(self, vendors: list[VendorRecord], purchase_orders: list[PurchaseOrder]) -> None:
        with self._connect() as conn:
            for vendor in vendors:
                conn.execute(
                    "INSERT OR REPLACE INTO vendors (vendor_id, canonical_name, aliases_json, approved) VALUES (?, ?, ?, ?)",
                    (vendor.vendor_id, vendor.canonical_name, json.dumps(vendor.aliases), int(vendor.approved)),
                )
            for po in purchase_orders:
                conn.execute(
                    "INSERT OR REPLACE INTO purchase_orders (po_id, vendor_id, amount, currency, issued_date, tax_treatment, invoiced_to_date) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (po.po_id, po.vendor_id, str(po.amount), po.currency, po.issued_date.isoformat(), po.tax_treatment, str(po.invoiced_to_date)),
                )

    def get_vendors(self) -> list[VendorRecord]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM vendors").fetchall()
        return [
            VendorRecord(
                vendor_id=row["vendor_id"],
                canonical_name=row["canonical_name"],
                aliases=json.loads(row["aliases_json"]),
                approved=bool(row["approved"]),
            )
            for row in rows
        ]

    def get_purchase_orders(self) -> list[PurchaseOrder]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM purchase_orders").fetchall()
        return [
            PurchaseOrder(
                po_id=row["po_id"],
                vendor_id=row["vendor_id"],
                amount=Decimal(row["amount"]),
                currency=row["currency"],
                issued_date=date.fromisoformat(row["issued_date"]),
                tax_treatment=row["tax_treatment"],
                invoiced_to_date=Decimal(row["invoiced_to_date"]),
            )
            for row in rows
        ]

    def update_po_balance(self, po_id: str, new_invoiced_to_date: Decimal) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE purchase_orders SET invoiced_to_date = ? WHERE po_id = ?", (str(new_invoiced_to_date), po_id))

    def create_run(self, run_id: str, doc_id: str, status: str) -> None:
        with self._connect() as conn:
            now = _now_iso()
            conn.execute(
                "INSERT INTO runs (run_id, doc_id, status, decision_json, created_at, updated_at) VALUES (?, ?, ?, NULL, ?, ?)",
                (run_id, doc_id, status, now, now),
            )

    def update_run_status(self, run_id: str, status: str) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE runs SET status = ?, updated_at = ? WHERE run_id = ?", (status, _now_iso(), run_id))

    def set_run_decision(self, run_id: str, decision: Decision) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE runs SET decision_json = ?, updated_at = ? WHERE run_id = ?",
                (decision.model_dump_json(), _now_iso(), run_id),
            )

    def append_stage(self, run_id: str, stage: str, payload: dict) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COALESCE(MAX(seq), -1) + 1 AS next_seq FROM run_stages WHERE run_id = ?", (run_id,)).fetchone()
            seq = row["next_seq"]
            conn.execute(
                "INSERT INTO run_stages (run_id, seq, stage, payload_json, created_at) VALUES (?, ?, ?, ?, ?)",
                (run_id, seq, stage, json.dumps(payload, default=str), _now_iso()),
            )
        return seq

    def get_stages(self, run_id: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM run_stages WHERE run_id = ? ORDER BY seq ASC", (run_id,)).fetchall()
        return [
            {"seq": row["seq"], "stage": row["stage"], "payload": json.loads(row["payload_json"]), "created_at": row["created_at"]}
            for row in rows
        ]

    def list_runs(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM runs ORDER BY created_at DESC").fetchall()
        return [self._run_row_to_dict(row) for row in rows]

    def get_run(self, run_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        return self._run_row_to_dict(row) if row is not None else None

    def _run_row_to_dict(self, row: sqlite3.Row) -> dict:
        return {
            "run_id": row["run_id"],
            "doc_id": row["doc_id"],
            "status": row["status"],
            "decision": json.loads(row["decision_json"]) if row["decision_json"] else None,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def save_invoice_record(self, run_id: str, vendor_id: str, invoice: Invoice) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO invoice_records (run_id, vendor_id, invoice_number, invoice_date, total_amount, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    vendor_id,
                    invoice.invoice_number,
                    invoice.invoice_date.isoformat() if invoice.invoice_date else None,
                    str(invoice.total_amount) if invoice.total_amount is not None else None,
                    _now_iso(),
                ),
            )

    def get_prior_invoices(self, vendor_id: str, exclude_run_id: str) -> list[Invoice]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM invoice_records WHERE vendor_id = ? AND run_id != ?",
                (vendor_id, exclude_run_id),
            ).fetchall()
        return [
            Invoice(
                invoice_number=row["invoice_number"],
                invoice_date=date.fromisoformat(row["invoice_date"]) if row["invoice_date"] else None,
                vendor_name_raw="",
                total_amount=Decimal(row["total_amount"]) if row["total_amount"] is not None else None,
                extraction_path="pdf_text",
                extraction_confidence=1.0,
            )
            for row in rows
        ]


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_storage.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add pipeline/storage.py tests/test_storage.py
git commit -m "feat: SQLite storage layer"
```

---

### Task 9: extraction confidence + router + pdf_text path

**Dependency note:** append `pymupdf` to `requirements.txt` in this task (`pip install pymupdf`) — needed to rasterize a scanned (no-text-layer) PDF page to an image for the vision path, without depending on system `poppler` (confirmed unavailable in this Windows environment earlier this session). Image attachments (`.png`/`.jpg`) never need this — they go to vision directly. `pymupdf` is a small, well-known library with prebuilt Windows wheels.

**Files:**
- Create: `pipeline/extraction/__init__.py`
- Create: `pipeline/extraction/confidence.py`
- Create: `pipeline/extraction/client.py`
- Create: `pipeline/extraction/pdf_text.py`
- Create: `pipeline/extraction/router.py`
- Modify: `requirements.txt` (add `pymupdf`)
- Test: `tests/test_extraction_confidence.py`
- Test: `tests/test_extraction_router.py`

**Interfaces:**
- Consumes: nothing from earlier tasks except reused conventions.
- Produces: `compute_confidence(raw: dict) -> float`; `ExtractionClient` Protocol with `structure_from_text(self, text: str) -> dict` and `structure_from_image(self, image_bytes: bytes, media_type: str) -> dict`; `route_and_extract(file_path: str, client: ExtractionClient) -> tuple[dict, str, float]` returning `(raw_fields, extraction_path, confidence)`, used by the graph (Task 12) and fed into `normalize_invoice` (Task 3). The real `AnthropicExtractionClient` implementing this Protocol is built in Task 10 — this task only depends on the Protocol's shape, so it's testable today with a fake.

- [ ] **Step 1: Write the failing tests**

`tests/test_extraction_confidence.py`:
```python
from pipeline.extraction.confidence import compute_confidence


def test_all_critical_fields_present_gives_full_confidence() -> None:
    raw = {"invoice_number": "INV-1", "invoice_date": "2026-08-02", "vendor_name": "Acme", "total_amount": 5000}
    assert compute_confidence(raw) == 1.0


def test_missing_invoice_number_reduces_confidence() -> None:
    raw = {"invoice_number": None, "invoice_date": "2026-08-02", "vendor_name": "Acme", "total_amount": 5000}
    assert compute_confidence(raw) == 0.75


def test_all_missing_gives_zero_confidence() -> None:
    assert compute_confidence({}) == 0.0
```

`tests/test_extraction_router.py`:
```python
from pathlib import Path

from reportlab.pdfgen import canvas

from pipeline.extraction.router import route_and_extract


class FakeClient:
    def __init__(self) -> None:
        self.text_calls: list[str] = []
        self.image_calls: list[tuple[bytes, str]] = []

    def structure_from_text(self, text: str) -> dict:
        self.text_calls.append(text)
        return {"invoice_number": "INV-1", "invoice_date": "2026-08-02", "vendor_name": "Acme", "total_amount": 5000}

    def structure_from_image(self, image_bytes: bytes, media_type: str) -> dict:
        self.image_calls.append((image_bytes, media_type))
        return {"invoice_number": None, "invoice_date": None, "vendor_name": "Acme", "total_amount": None}


def _make_text_pdf(path: Path) -> None:
    c = canvas.Canvas(str(path))
    c.drawString(72, 720, "Invoice INV-1 from Acme Corp, total 5000 USD")
    c.save()


def test_text_pdf_routes_to_pdf_text_path(tmp_path: Path) -> None:
    pdf_path = tmp_path / "clean.pdf"
    _make_text_pdf(pdf_path)
    client = FakeClient()
    raw, extraction_path, confidence = route_and_extract(str(pdf_path), client)
    assert extraction_path == "pdf_text"
    assert len(client.text_calls) == 1
    assert "Acme" in client.text_calls[0]
    assert confidence == 1.0


def test_image_attachment_routes_to_vision_path(tmp_path: Path) -> None:
    from PIL import Image

    img_path = tmp_path / "scan.png"
    Image.new("RGB", (100, 100), color="white").save(img_path)
    client = FakeClient()
    raw, extraction_path, confidence = route_and_extract(str(img_path), client)
    assert extraction_path == "vision"
    assert len(client.image_calls) == 1
    assert client.image_calls[0][1] == "image/png"


def test_scanned_pdf_with_no_text_layer_routes_to_vision_path(tmp_path: Path) -> None:
    pdf_path = tmp_path / "blank.pdf"
    c = canvas.Canvas(str(pdf_path))
    c.showPage()
    c.save()
    client = FakeClient()
    raw, extraction_path, confidence = route_and_extract(str(pdf_path), client)
    assert extraction_path == "vision"
    assert len(client.image_calls) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_extraction_confidence.py tests/test_extraction_router.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pipeline.extraction'`.

- [ ] **Step 3: Add pymupdf to requirements.txt and install**

Append `pymupdf` as a new line to `requirements.txt`, then run: `pip install pymupdf`

- [ ] **Step 4: Write pipeline/extraction/__init__.py (empty)**

- [ ] **Step 5: Write pipeline/extraction/confidence.py**

```python
CRITICAL_FIELDS = ["invoice_number", "invoice_date", "vendor_name", "total_amount"]


def compute_confidence(raw: dict) -> float:
    present = sum(1 for field_name in CRITICAL_FIELDS if raw.get(field_name) not in (None, ""))
    return present / len(CRITICAL_FIELDS)
```

- [ ] **Step 6: Write pipeline/extraction/client.py**

```python
from typing import Protocol


class ExtractionClient(Protocol):
    def structure_from_text(self, text: str) -> dict: ...
    def structure_from_image(self, image_bytes: bytes, media_type: str) -> dict: ...


EXTRACTION_INSTRUCTIONS = (
    "Extract invoice fields as JSON with exactly these keys: invoice_number, "
    "invoice_date (YYYY-MM-DD), vendor_name, po_reference, line_items (list of "
    "objects with description, quantity, unit_price, line_total), subtotal, "
    "tax_amount, total_amount, currency. Use null for any field not present in "
    "the document. Return only the JSON object, no surrounding prose."
)
```

- [ ] **Step 7: Write pipeline/extraction/pdf_text.py**

```python
import pdfplumber

MIN_TEXT_LENGTH = 20


def has_text_layer(pdf_path: str) -> bool:
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            if len(text.strip()) >= MIN_TEXT_LENGTH:
                return True
    return False


def extract_raw_text(pdf_path: str) -> str:
    with pdfplumber.open(pdf_path) as pdf:
        pages_text = [page.extract_text() or "" for page in pdf.pages]
    return "\n".join(pages_text)
```

- [ ] **Step 8: Write pipeline/extraction/router.py**

```python
from pathlib import Path

import fitz

from pipeline.extraction.client import ExtractionClient
from pipeline.extraction.confidence import compute_confidence
from pipeline.extraction.pdf_text import extract_raw_text, has_text_layer

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg"}
IMAGE_MEDIA_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}


def route_and_extract(file_path: str, client: ExtractionClient) -> tuple[dict, str, float]:
    path = Path(file_path)
    suffix = path.suffix.lower()

    if suffix in IMAGE_EXTENSIONS:
        image_bytes = path.read_bytes()
        raw = client.structure_from_image(image_bytes, IMAGE_MEDIA_TYPES[suffix])
        return raw, "vision", compute_confidence(raw)

    if suffix == ".pdf":
        if has_text_layer(file_path):
            text = extract_raw_text(file_path)
            raw = client.structure_from_text(text)
            return raw, "pdf_text", compute_confidence(raw)
        image_bytes = _rasterize_first_page(file_path)
        raw = client.structure_from_image(image_bytes, "image/png")
        return raw, "vision", compute_confidence(raw)

    raise ValueError(f"unsupported attachment type: {suffix}")


def _rasterize_first_page(pdf_path: str) -> bytes:
    document = fitz.open(pdf_path)
    try:
        page = document.load_page(0)
        pixmap = page.get_pixmap(dpi=200)
        return pixmap.tobytes("png")
    finally:
        document.close()
```

- [ ] **Step 9: Run tests to verify they pass**

Run: `pytest tests/test_extraction_confidence.py tests/test_extraction_router.py -v`
Expected: PASS (6 tests).

- [ ] **Step 10: Commit**

```bash
git add pipeline/extraction requirements.txt tests/test_extraction_confidence.py tests/test_extraction_router.py
git commit -m "feat: hybrid extraction router (pdfplumber text + vision fallback)"
```

---

### Task 10: real Anthropic-backed extraction client + triage

**Files:**
- Create: `pipeline/extraction/json_parsing.py`
- Create: `pipeline/extraction/vision.py`
- Create: `pipeline/triage.py`
- Test: `tests/test_json_parsing.py`
- Test: `tests/test_triage.py`

**Interfaces:**
- Consumes: `pipeline.extraction.client.{ExtractionClient, EXTRACTION_INSTRUCTIONS}`, `pipeline.schemas.TriageResult`, `anthropic.Anthropic`.
- Produces: `parse_json_response(raw_text: str) -> dict` (pure, unit-tested); `AnthropicExtractionClient` implementing `ExtractionClient`, used by the graph (Task 12) as the real client; `TriageClient` Protocol with `triage_text(self, text: str) -> dict` / `triage_image(self, image_bytes: bytes, media_type: str) -> dict`; `run_triage(file_path: str, client: TriageClient) -> TriageResult` (plumbing, unit-tested with a fake); `AnthropicTriageClient` implementing `TriageClient`, used by the graph as the real client.

The two Anthropic-backed classes (`AnthropicExtractionClient`, `AnthropicTriageClient`) make real network calls and are **not** covered by the automated pytest suite — that would require a live API key, cost money, and be non-deterministic in CI. Step 8 below is a manual smoke test instead: real output you read and judge, not a guess.

- [ ] **Step 1: Write the failing test for JSON parsing**

```python
import pytest

from pipeline.extraction.json_parsing import parse_json_response


def test_parses_plain_json() -> None:
    result = parse_json_response('{"invoice_number": "INV-1"}')
    assert result == {"invoice_number": "INV-1"}


def test_strips_markdown_json_fence() -> None:
    result = parse_json_response('```json\n{"invoice_number": "INV-1"}\n```')
    assert result == {"invoice_number": "INV-1"}


def test_strips_plain_markdown_fence() -> None:
    result = parse_json_response('```\n{"invoice_number": "INV-1"}\n```')
    assert result == {"invoice_number": "INV-1"}


def test_invalid_json_raises_value_error_with_context() -> None:
    with pytest.raises(ValueError, match="did not return valid JSON"):
        parse_json_response("not json at all")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_json_parsing.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pipeline.extraction.json_parsing'`.

- [ ] **Step 3: Write pipeline/extraction/json_parsing.py**

```python
import json


def parse_json_response(raw_text: str) -> dict:
    cleaned = _strip_markdown_fence(raw_text.strip())
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError(f"model did not return valid JSON: {cleaned[:200]!r}") from exc


def _strip_markdown_fence(text: str) -> str:
    if not text.startswith("```"):
        return text
    lines = text.splitlines()[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_json_parsing.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Write pipeline/extraction/vision.py**

```python
import base64
import os

from anthropic import Anthropic

from pipeline.extraction.client import EXTRACTION_INSTRUCTIONS
from pipeline.extraction.json_parsing import parse_json_response

DEFAULT_MODEL = "claude-sonnet-5"


class AnthropicExtractionClient:
    def __init__(self, api_key: str | None = None, model: str = DEFAULT_MODEL) -> None:
        resolved_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not resolved_key:
            raise ValueError("ANTHROPIC_API_KEY is not set; add it to .env before running extraction")
        self._client = Anthropic(api_key=resolved_key)
        self._model = model

    def structure_from_text(self, text: str) -> dict:
        response = self._client.messages.create(
            model=self._model,
            max_tokens=1024,
            messages=[{"role": "user", "content": f"{EXTRACTION_INSTRUCTIONS}\n\nInvoice text:\n{text}"}],
        )
        return parse_json_response(text_from_response(response))

    def structure_from_image(self, image_bytes: bytes, media_type: str) -> dict:
        encoded = base64.standard_b64encode(image_bytes).decode("utf-8")
        response = self._client.messages.create(
            model=self._model,
            max_tokens=1024,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": encoded}},
                        {"type": "text", "text": EXTRACTION_INSTRUCTIONS},
                    ],
                }
            ],
        )
        return parse_json_response(text_from_response(response))


def text_from_response(response: object) -> str:
    return "".join(block.text for block in response.content if block.type == "text")
```

- [ ] **Step 6: Write the failing test for triage plumbing**

```python
from pathlib import Path

from reportlab.pdfgen import canvas

from pipeline.triage import run_triage


class FakeTriageClient:
    def __init__(self, response: dict) -> None:
        self._response = response
        self.text_calls: list[str] = []
        self.image_calls: list[tuple[bytes, str]] = []

    def triage_text(self, text: str) -> dict:
        self.text_calls.append(text)
        return self._response

    def triage_image(self, image_bytes: bytes, media_type: str) -> dict:
        self.image_calls.append((image_bytes, media_type))
        return self._response


def test_run_triage_uses_text_path_for_text_pdf(tmp_path: Path) -> None:
    pdf_path = tmp_path / "clean.pdf"
    c = canvas.Canvas(str(pdf_path))
    c.drawString(72, 720, "Invoice INV-1 from Acme Corp, total 5000 USD")
    c.save()
    client = FakeTriageClient({"looks_like_invoice": True, "preview_text": "Invoice from Acme", "reason": "has invoice number and total"})
    result = run_triage(str(pdf_path), client)
    assert result.looks_like_invoice is True
    assert len(client.text_calls) == 1
    assert len(client.image_calls) == 0


def test_run_triage_uses_image_path_for_image_attachment(tmp_path: Path) -> None:
    from PIL import Image

    img_path = tmp_path / "scan.png"
    Image.new("RGB", (100, 100), color="white").save(img_path)
    client = FakeTriageClient({"looks_like_invoice": False, "preview_text": "blank image", "reason": "no readable content"})
    result = run_triage(str(img_path), client)
    assert result.looks_like_invoice is False
    assert len(client.image_calls) == 1
```

- [ ] **Step 7: Run test to verify it fails, then write pipeline/triage.py**

Run: `pytest tests/test_triage.py -v` → FAIL with `ModuleNotFoundError: No module named 'pipeline.triage'`.

```python
import base64
import os
from pathlib import Path
from typing import Protocol

from anthropic import Anthropic

from pipeline.extraction.json_parsing import parse_json_response
from pipeline.extraction.pdf_text import extract_raw_text, has_text_layer
from pipeline.extraction.vision import DEFAULT_MODEL, text_from_response
from pipeline.schemas import TriageResult

TRIAGE_INSTRUCTIONS = (
    "Look at this document and decide whether it is a vendor invoice. Return "
    "JSON with exactly these keys: looks_like_invoice (boolean), preview_text "
    "(a one or two sentence human-readable summary of what this document is), "
    "reason (why you decided that). Return only the JSON object, no prose."
)

IMAGE_MEDIA_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}


class TriageClient(Protocol):
    def triage_text(self, text: str) -> dict: ...
    def triage_image(self, image_bytes: bytes, media_type: str) -> dict: ...


def run_triage(file_path: str, client: TriageClient) -> TriageResult:
    path = Path(file_path)
    suffix = path.suffix.lower()

    if suffix in IMAGE_MEDIA_TYPES:
        raw = client.triage_image(path.read_bytes(), IMAGE_MEDIA_TYPES[suffix])
        return TriageResult.model_validate(raw)

    if suffix == ".pdf" and has_text_layer(file_path):
        raw = client.triage_text(extract_raw_text(file_path))
        return TriageResult.model_validate(raw)

    if suffix == ".pdf":
        raw = client.triage_image(path.read_bytes(), "application/pdf")
        return TriageResult.model_validate(raw)

    raise ValueError(f"unsupported attachment type for triage: {suffix}")


class AnthropicTriageClient:
    def __init__(self, api_key: str | None = None, model: str = DEFAULT_MODEL) -> None:
        resolved_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not resolved_key:
            raise ValueError("ANTHROPIC_API_KEY is not set; add it to .env before running triage")
        self._client = Anthropic(api_key=resolved_key)
        self._model = model

    def triage_text(self, text: str) -> dict:
        response = self._client.messages.create(
            model=self._model,
            max_tokens=512,
            messages=[{"role": "user", "content": f"{TRIAGE_INSTRUCTIONS}\n\nDocument text:\n{text}"}],
        )
        return parse_json_response(text_from_response(response))

    def triage_image(self, image_bytes: bytes, media_type: str) -> dict:
        encoded = base64.standard_b64encode(image_bytes).decode("utf-8")
        response = self._client.messages.create(
            model=self._model,
            max_tokens=512,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": encoded}},
                        {"type": "text", "text": TRIAGE_INSTRUCTIONS},
                    ],
                }
            ],
        )
        return parse_json_response(text_from_response(response))
```

`AnthropicTriageClient.triage_image` sends a PDF as an inline image block for the no-text-layer PDF case — if the installed Anthropic SDK rejects `application/pdf` as an image media type, switch that one call to the SDK's native PDF document content block (`{"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": encoded}}`) instead; check `pip show anthropic` for the installed version's supported content types before assuming either shape.

- [ ] **Step 8: Run tests to verify they pass**

Run: `pytest tests/test_json_parsing.py tests/test_triage.py -v`
Expected: PASS (6 tests).

- [ ] **Step 9: Manual smoke test of the real Anthropic clients**

Set `ANTHROPIC_API_KEY` in `.env`. Run:
```bash
python -c "
from pipeline.extraction.vision import AnthropicExtractionClient
client = AnthropicExtractionClient()
print(client.structure_from_text('Invoice INV-9999 from Acme Corp dated 2026-08-02, total 500.00 USD'))
"
```
Expected: a printed dict with `invoice_number: 'INV-9999'`, `vendor_name` containing 'Acme', `total_amount: 500.00` (or `500`). This is the inspectable, real-output check — read what came back, don't assume it worked.

- [ ] **Step 10: Commit**

```bash
git add pipeline/extraction pipeline/triage.py tests/test_json_parsing.py tests/test_triage.py
git commit -m "feat: real Claude-backed extraction and triage clients"
```

---

### Task 11: generated invoice fixtures — happy path + edge cases A–D

**Files:**
- Create: `data/generate_fixtures.py`
- Create: `data/invoices/` (output directory, generated by the script)
- Test: `tests/test_generate_fixtures.py`

**Interfaces:**
- Consumes: `reportlab.pdfgen.canvas`, `PIL.Image`/`ImageDraw`/`ImageFilter`.
- Produces: on running `python -m data.generate_fixtures`, these files under `data/invoices/`: `happy_path.pdf` (INV-3001, PO-1001, $5000), `edge_a_split_1.pdf` / `edge_a_split_2.pdf` (INV-4001 $6000 / INV-4002 $4000, PO-1002), `edge_b_tax.pdf` (INV-5001, PO-1003, subtotal $3000 + $240 tax = $3240 total), `edge_c_dup_1.pdf` / `edge_c_dup_2.pdf` (INV-6001 $2000 / INV-6002 $1995, PO-1004, 3 days apart), `edge_d_degraded.png` (deliberately illegible scan). These map 1:1 to the golden eval cases in Task 18.

- [ ] **Step 1: Write the failing test**

```python
from pathlib import Path

from pdfplumber import open as open_pdf

INVOICE_DIR = Path(__file__).resolve().parent.parent / "data" / "invoices"


def test_happy_path_pdf_contains_expected_fields() -> None:
    with open_pdf(INVOICE_DIR / "happy_path.pdf") as pdf:
        text = pdf.pages[0].extract_text()
    assert "INV-3001" in text
    assert "PO-1001" in text
    assert "5000" in text


def test_edge_a_split_invoices_reference_same_po_with_partial_amounts() -> None:
    with open_pdf(INVOICE_DIR / "edge_a_split_1.pdf") as pdf:
        text_1 = pdf.pages[0].extract_text()
    with open_pdf(INVOICE_DIR / "edge_a_split_2.pdf") as pdf:
        text_2 = pdf.pages[0].extract_text()
    assert "PO-1002" in text_1 and "6000" in text_1
    assert "PO-1002" in text_2 and "4000" in text_2


def test_edge_b_tax_invoice_shows_subtotal_tax_and_total() -> None:
    with open_pdf(INVOICE_DIR / "edge_b_tax.pdf") as pdf:
        text = pdf.pages[0].extract_text()
    assert "3000" in text
    assert "240" in text
    assert "3240" in text


def test_edge_c_duplicate_invoices_are_close_but_distinct() -> None:
    with open_pdf(INVOICE_DIR / "edge_c_dup_1.pdf") as pdf:
        text_1 = pdf.pages[0].extract_text()
    with open_pdf(INVOICE_DIR / "edge_c_dup_2.pdf") as pdf:
        text_2 = pdf.pages[0].extract_text()
    assert "INV-6001" in text_1
    assert "INV-6002" in text_2
    assert "1995" in text_2


def test_edge_d_degraded_scan_exists_as_image() -> None:
    from PIL import Image

    with Image.open(INVOICE_DIR / "edge_d_degraded.png") as img:
        assert img.format == "PNG"
        assert img.size[0] > 100 and img.size[1] > 100
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_generate_fixtures.py -v`
Expected: FAIL with `FileNotFoundError` (the `data/invoices/` directory doesn't exist yet).

- [ ] **Step 3: Write data/generate_fixtures.py**

```python
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont
from reportlab.pdfgen import canvas

OUTPUT_DIR = Path(__file__).resolve().parent / "invoices"


def draw_invoice_pdf(
    path: Path,
    invoice_number: str,
    invoice_date: str,
    vendor_name: str,
    po_reference: str,
    line_items: list[tuple[str, str, str, str]],
    subtotal: str,
    tax_amount: str,
    total_amount: str,
) -> None:
    c = canvas.Canvas(str(path))
    y = 760
    c.setFont("Helvetica-Bold", 14)
    c.drawString(72, y, f"INVOICE {invoice_number}")
    c.setFont("Helvetica", 11)
    y -= 24
    c.drawString(72, y, f"Date: {invoice_date}")
    y -= 18
    c.drawString(72, y, f"Vendor: {vendor_name}")
    y -= 18
    c.drawString(72, y, f"PO Reference: {po_reference}")
    y -= 30
    c.setFont("Helvetica-Bold", 11)
    c.drawString(72, y, "Description")
    c.drawString(300, y, "Qty")
    c.drawString(360, y, "Unit Price")
    c.drawString(460, y, "Line Total")
    y -= 16
    c.setFont("Helvetica", 11)
    for description, quantity, unit_price, line_total in line_items:
        c.drawString(72, y, description)
        c.drawString(300, y, quantity)
        c.drawString(360, y, unit_price)
        c.drawString(460, y, line_total)
        y -= 16
    y -= 20
    c.drawString(360, y, f"Subtotal: {subtotal}")
    y -= 16
    c.drawString(360, y, f"Tax: {tax_amount}")
    y -= 16
    c.setFont("Helvetica-Bold", 11)
    c.drawString(360, y, f"Total: {total_amount}")
    c.save()


def make_degraded_scan(path: Path) -> None:
    image = Image.new("L", (900, 1200), color=235)
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default()
    draw.text((60, 80), "INVOICE", fill=80, font=font)
    draw.text((60, 110), "Vendor: Acme Corp", fill=90, font=font)
    draw.text((60, 130), "Total: 1500.00 USD", fill=90, font=font)
    draw.text((60, 150), "Invoice #: (smudged)", fill=110, font=font)
    for offset in range(0, 1200, 7):
        draw.line([(0, offset), (900, offset)], fill=200, width=1)
    image = image.rotate(9, expand=True, fillcolor=235)
    image = image.filter(ImageFilter.GaussianBlur(radius=3.5))
    image.convert("RGB").save(path, format="PNG")


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    draw_invoice_pdf(
        OUTPUT_DIR / "happy_path.pdf",
        invoice_number="INV-3001",
        invoice_date="2026-08-02",
        vendor_name="Acme Corp",
        po_reference="PO-1001",
        line_items=[("Consulting services", "1", "5000.00", "5000.00")],
        subtotal="5000.00",
        tax_amount="0.00",
        total_amount="5000.00",
    )

    draw_invoice_pdf(
        OUTPUT_DIR / "edge_a_split_1.pdf",
        invoice_number="INV-4001",
        invoice_date="2026-08-06",
        vendor_name="Globex Industries LLC",
        po_reference="PO-1002",
        line_items=[("Phase 1 delivery (60%)", "1", "6000.00", "6000.00")],
        subtotal="6000.00",
        tax_amount="0.00",
        total_amount="6000.00",
    )
    draw_invoice_pdf(
        OUTPUT_DIR / "edge_a_split_2.pdf",
        invoice_number="INV-4002",
        invoice_date="2026-08-20",
        vendor_name="Globex Industries LLC",
        po_reference="PO-1002",
        line_items=[("Phase 2 delivery (40%)", "1", "4000.00", "4000.00")],
        subtotal="4000.00",
        tax_amount="0.00",
        total_amount="4000.00",
    )

    draw_invoice_pdf(
        OUTPUT_DIR / "edge_b_tax.pdf",
        invoice_number="INV-5001",
        invoice_date="2026-08-11",
        vendor_name="Acme Corp",
        po_reference="PO-1003",
        line_items=[("Annual license", "1", "3000.00", "3000.00")],
        subtotal="3000.00",
        tax_amount="240.00",
        total_amount="3240.00",
    )

    draw_invoice_pdf(
        OUTPUT_DIR / "edge_c_dup_1.pdf",
        invoice_number="INV-6001",
        invoice_date="2026-08-13",
        vendor_name="Acme Corp",
        po_reference="PO-1004",
        line_items=[("Support retainer", "1", "2000.00", "2000.00")],
        subtotal="2000.00",
        tax_amount="0.00",
        total_amount="2000.00",
    )
    draw_invoice_pdf(
        OUTPUT_DIR / "edge_c_dup_2.pdf",
        invoice_number="INV-6002",
        invoice_date="2026-08-16",
        vendor_name="Acme Corp",
        po_reference="PO-1004",
        line_items=[("Support retainer", "1", "1995.00", "1995.00")],
        subtotal="1995.00",
        tax_amount="0.00",
        total_amount="1995.00",
    )

    make_degraded_scan(OUTPUT_DIR / "edge_d_degraded.png")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the generator**

Run: `python -m data.generate_fixtures`
Expected: seven files created under `data/invoices/` (5 PDFs from happy path + edge A/B/C, no wait — count: happy_path.pdf, edge_a_split_1.pdf, edge_a_split_2.pdf, edge_b_tax.pdf, edge_c_dup_1.pdf, edge_c_dup_2.pdf, edge_d_degraded.png = 7 files total).

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_generate_fixtures.py -v`
Expected: PASS (5 tests).

- [ ] **Step 6: Commit**

```bash
git add data/generate_fixtures.py data/invoices tests/test_generate_fixtures.py
git commit -m "feat: generated invoice fixtures for happy path and edge cases"
```

---

### Task 12: LangGraph wiring — the durable human gate + full pipeline

**Version check first — LangGraph's interrupt/resume API is the single highest version-risk item in this plan.** Before writing code, run:
```bash
python -c "import langgraph; print(langgraph.__version__)"
python -c "from langgraph.types import interrupt, Command; print('interrupt/Command import OK')"
python -c "from langgraph.checkpoint.sqlite import SqliteSaver; print('SqliteSaver import OK')"
```
The code below is written against the current stable shape of these imports. If any import fails, run `pip show langgraph langgraph-checkpoint-sqlite` and check that package's own README/changelog for the current import path — the mechanism to preserve is "a node can pause the graph and a later call resumes it with an external value, and a checkpointer persists that pause across process restarts," not the exact import path.

**Files:**
- Create: `pipeline/graph.py`
- Test: `tests/test_graph_happy_path.py`

**Interfaces:**
- Consumes: every component built in Tasks 3–10, `pipeline.storage.Repository`, `pipeline.extraction.client.ExtractionClient`, `pipeline.triage.TriageClient`.
- Produces: `build_graph(repo: Repository, extraction_client, triage_client, checkpoint_db_path: str, on_stage: Callable[[str, str, dict], None] | None = None)` returning a compiled, checkpointed LangGraph app — `on_stage(run_id, stage_name, payload)` fires immediately after each stage persists, so the web server (Task 15) can push it straight onto an SSE stream instead of the UI having to poll; `start_run(app, run_id: str, document: IncomingDocument, repo: Repository) -> dict`; `resume_run(app, run_id: str, approved: bool) -> dict`. Used by the web server (Task 15) and rerun CLI (Task 17).

- [ ] **Step 1: Write the failing test**

```python
from pathlib import Path

from pipeline.graph import build_graph, resume_run, start_run
from pipeline.schemas import IncomingDocument
from pipeline.storage import Repository

FIXTURE = Path(__file__).resolve().parent.parent / "data" / "invoices" / "happy_path.pdf"


class FakeExtractionClient:
    def structure_from_text(self, text: str) -> dict:
        return {
            "invoice_number": "INV-3001",
            "invoice_date": "2026-08-02",
            "vendor_name": "Acme Corp",
            "po_reference": "PO-1001",
            "line_items": [{"description": "Consulting", "quantity": 1, "unit_price": 5000, "line_total": 5000}],
            "subtotal": 5000,
            "tax_amount": 0,
            "total_amount": 5000,
            "currency": "USD",
        }

    def structure_from_image(self, image_bytes: bytes, media_type: str) -> dict:
        raise AssertionError("happy path fixture has a text layer; vision path should not be called")


class FakeTriageClient:
    def triage_text(self, text: str) -> dict:
        return {"looks_like_invoice": True, "preview_text": "Invoice from Acme Corp", "reason": "has invoice number and total"}

    def triage_image(self, image_bytes: bytes, media_type: str) -> dict:
        raise AssertionError("happy path fixture has a text layer; vision path should not be called")


def _seeded_repo(tmp_path: Path) -> Repository:
    from datetime import date
    from decimal import Decimal

    from pipeline.schemas import PurchaseOrder, VendorRecord

    repo = Repository(str(tmp_path / "test.db"))
    repo.init_db()
    repo.seed_reference_data(
        [VendorRecord(vendor_id="V-ACME", canonical_name="Acme Corporation Pvt Ltd", aliases=["Acme Corp"], approved=True)],
        [PurchaseOrder(po_id="PO-1001", vendor_id="V-ACME", amount=Decimal("5000"), issued_date=date(2026, 8, 1), tax_treatment="exclusive")],
    )
    return repo


def test_happy_path_pauses_at_gate_then_auto_approves_on_resume(tmp_path: Path) -> None:
    repo = _seeded_repo(tmp_path)
    app = build_graph(repo, FakeExtractionClient(), FakeTriageClient(), str(tmp_path / "checkpoints.db"))
    document = IncomingDocument(doc_id="doc-1", source="folder", received_at="2026-08-02T00:00:00Z", sender=None, filename="happy_path.pdf", content_path=str(FIXTURE))

    start_run(app, "run-1", document, repo)
    run_after_start = repo.get_run("run-1")
    assert run_after_start["status"] == "AWAITING_APPROVAL"

    result = resume_run(app, "run-1", approved=True)
    assert result["decision"].outcome == "AUTO_APPROVE"
    run_after_resume = repo.get_run("run-1")
    assert run_after_resume["status"] == "DONE"
    assert run_after_resume["decision"]["outcome"] == "AUTO_APPROVE"

    updated_pos = {po.po_id: po for po in repo.get_purchase_orders()}
    assert updated_pos["PO-1001"].invoiced_to_date == updated_pos["PO-1001"].amount


def test_rejected_at_gate_never_reaches_extraction(tmp_path: Path) -> None:
    repo = _seeded_repo(tmp_path)
    app = build_graph(repo, FakeExtractionClient(), FakeTriageClient(), str(tmp_path / "checkpoints.db"))
    document = IncomingDocument(doc_id="doc-2", source="folder", received_at="2026-08-02T00:00:00Z", sender=None, filename="happy_path.pdf", content_path=str(FIXTURE))

    start_run(app, "run-2", document, repo)
    resume_run(app, "run-2", approved=False)
    run_after = repo.get_run("run-2")
    assert run_after["status"] == "REJECTED_AT_GATE"
    assert run_after["decision"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_graph_happy_path.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pipeline.graph'`.

- [ ] **Step 3: Write pipeline/graph.py**

```python
from typing import Any, Callable, TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, StateGraph
from langgraph.types import Command, interrupt

from pipeline.decision import decide
from pipeline.extraction.client import ExtractionClient
from pipeline.extraction.router import route_and_extract
from pipeline.normalize import normalize_invoice
from pipeline.po_match import match_po
from pipeline.schemas import Decision, IncomingDocument, Invoice, MatchResult, TriageResult, VendorRecord
from pipeline.storage import Repository
from pipeline.triage import TriageClient, run_triage
from pipeline.validate import validate_invoice
from pipeline.vendor_resolve import resolve_vendor


class GraphState(TypedDict, total=False):
    document: IncomingDocument
    run_id: str
    triage: TriageResult
    approved: bool
    raw_extraction: dict
    extraction_path: str
    extraction_confidence: float
    invoice: Invoice
    vendor: VendorRecord | None
    match: MatchResult
    decision: Decision


def build_graph(
    repo: Repository,
    extraction_client: ExtractionClient,
    triage_client: TriageClient,
    checkpoint_db_path: str,
    on_stage: Callable[[str, str, dict], None] | None = None,
) -> Any:
    def record(run_id: str, stage: str, payload: dict) -> None:
        repo.append_stage(run_id, stage, payload)
        if on_stage is not None:
            on_stage(run_id, stage, payload)

    def node_triage(state: GraphState) -> dict:
        triage_result = run_triage(state["document"].content_path, triage_client)
        record(state["run_id"], "triage", triage_result.model_dump())
        return {"triage": triage_result}

    def node_gate(state: GraphState) -> dict:
        repo.update_run_status(state["run_id"], "AWAITING_APPROVAL")
        decision_value = interrupt({"doc_id": state["document"].doc_id, "preview": state["triage"].preview_text})
        approved = decision_value == "approve"
        repo.update_run_status(state["run_id"], "RUNNING" if approved else "REJECTED_AT_GATE")
        record(state["run_id"], "gate", {"approved": approved})
        if not approved and on_stage is not None:
            on_stage(state["run_id"], "__done__", {})
        return {"approved": approved}

    def route_after_gate(state: GraphState) -> str:
        return "extract" if state["approved"] else END

    def node_extract(state: GraphState) -> dict:
        raw, extraction_path, confidence = route_and_extract(state["document"].content_path, extraction_client)
        record(state["run_id"], "extract", {"raw": raw, "extraction_path": extraction_path, "confidence": confidence})
        return {"raw_extraction": raw, "extraction_path": extraction_path, "extraction_confidence": confidence}

    def node_normalize(state: GraphState) -> dict:
        invoice = normalize_invoice(state["raw_extraction"], state["extraction_path"], state["extraction_confidence"])
        record(state["run_id"], "normalize", invoice.model_dump(mode="json"))
        return {"invoice": invoice}

    def node_validate(state: GraphState) -> dict:
        invoice = validate_invoice(state["invoice"])
        record(state["run_id"], "validate", invoice.model_dump(mode="json"))
        return {"invoice": invoice}

    def node_vendor_resolve(state: GraphState) -> dict:
        vendor = resolve_vendor(state["invoice"].vendor_name_raw, repo.get_vendors())
        record(state["run_id"], "vendor_resolve", {"vendor": vendor.model_dump() if vendor else None})
        if vendor is not None:
            repo.save_invoice_record(state["run_id"], vendor.vendor_id, state["invoice"])
        return {"vendor": vendor}

    def node_po_match(state: GraphState) -> dict:
        vendor = state.get("vendor")
        prior_invoices = repo.get_prior_invoices(vendor.vendor_id, state["run_id"]) if vendor is not None else []
        match = match_po(state["invoice"], vendor, repo.get_purchase_orders(), prior_invoices)
        record(state["run_id"], "po_match", match.model_dump(mode="json"))
        return {"match": match}

    def node_decide(state: GraphState) -> dict:
        decision = decide(state["invoice"], state["match"])
        record(state["run_id"], "decide", decision.model_dump(mode="json"))
        repo.set_run_decision(state["run_id"], decision)
        repo.update_run_status(state["run_id"], "DONE")
        match = state["match"]
        if decision.outcome == "AUTO_APPROVE" and match.po is not None and match.comparison_amount is not None:
            new_invoiced_to_date = match.po.invoiced_to_date + match.comparison_amount
            repo.update_po_balance(match.po.po_id, new_invoiced_to_date)
        if on_stage is not None:
            on_stage(state["run_id"], "__done__", {})
        return {"decision": decision}

    graph = StateGraph(GraphState)
    for name, fn in [
        ("triage", node_triage),
        ("gate", node_gate),
        ("extract", node_extract),
        ("normalize", node_normalize),
        ("validate", node_validate),
        ("vendor_resolve", node_vendor_resolve),
        ("po_match", node_po_match),
        ("decide", node_decide),
    ]:
        graph.add_node(name, fn)

    graph.set_entry_point("triage")
    graph.add_edge("triage", "gate")
    graph.add_conditional_edges("gate", route_after_gate, {"extract": "extract", END: END})
    graph.add_edge("extract", "normalize")
    graph.add_edge("normalize", "validate")
    graph.add_edge("validate", "vendor_resolve")
    graph.add_edge("vendor_resolve", "po_match")
    graph.add_edge("po_match", "decide")
    graph.add_edge("decide", END)

    checkpointer_ctx = SqliteSaver.from_conn_string(checkpoint_db_path)
    checkpointer = checkpointer_ctx.__enter__()
    compiled = graph.compile(checkpointer=checkpointer)
    compiled._checkpointer_ctx = checkpointer_ctx
    return compiled


def start_run(app: Any, run_id: str, document: IncomingDocument, repo: Repository) -> dict:
    repo.create_run(run_id, document.doc_id, "RUNNING")
    config = {"configurable": {"thread_id": run_id}}
    return app.invoke({"document": document, "run_id": run_id}, config=config)


def resume_run(app: Any, run_id: str, approved: bool) -> dict:
    config = {"configurable": {"thread_id": run_id}}
    return app.invoke(Command(resume="approve" if approved else "reject"), config=config)
```

`SqliteSaver.from_conn_string(...)` is a context manager in some LangGraph versions (hence `__enter__` above, kept open for the process lifetime — acceptable for this single-process demo app) and a plain constructor in others. If `.from_conn_string(...)` returns a saver directly (no `__enter__` needed), drop the `checkpointer_ctx`/`__enter__` line and use its return value directly — run the Step 1 version-check commands above to confirm which shape is installed before assuming either.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_graph_happy_path.py -v`
Expected: PASS (2 tests). If `interrupt()`/`Command` behave differently than expected (e.g. `app.invoke` raises instead of returning a paused state, or the resume value needs a different shape), use `app.get_state(config)` to inspect the actual paused state — printed, inspected, not guessed — and adjust `node_gate`/`resume_run` to match what's actually installed.

- [ ] **Step 5: Commit**

```bash
git add pipeline/graph.py tests/test_graph_happy_path.py
git commit -m "feat: LangGraph pipeline with durable human gate"
```

---

### Task 13: intake — folder watcher + upload source

**Files:**
- Create: `pipeline/intake/__init__.py`
- Create: `pipeline/intake/base.py`
- Create: `pipeline/intake/folder_source.py`
- Create: `pipeline/intake/upload_source.py`
- Test: `tests/test_intake_folder.py`
- Test: `tests/test_intake_upload.py`

**Interfaces:**
- Consumes: `pipeline.schemas.IncomingDocument`.
- Produces: `IntakeSource` Protocol with `poll(self) -> list[IncomingDocument]`; `FolderSource(watch_dir: str, storage_dir: str)` implementing it; `UploadSource(storage_dir: str)` with `save(self, filename: str, content: bytes) -> IncomingDocument`. Used by the web server (Task 15) and a background poll loop.

- [ ] **Step 1: Write the failing tests**

```python
from pathlib import Path

from pipeline.intake.folder_source import FolderSource


def test_poll_picks_up_supported_files_and_moves_them(tmp_path: Path) -> None:
    watch_dir = tmp_path / "watch"
    storage_dir = tmp_path / "storage"
    watch_dir.mkdir()
    (watch_dir / "invoice.pdf").write_bytes(b"%PDF-1.4 fake")
    (watch_dir / "notes.txt").write_text("ignore me")

    source = FolderSource(str(watch_dir), str(storage_dir))
    documents = source.poll()

    assert len(documents) == 1
    assert documents[0].source == "folder"
    assert documents[0].filename == "invoice.pdf"
    assert Path(documents[0].content_path).exists()
    assert not (watch_dir / "invoice.pdf").exists()
    assert (watch_dir / "notes.txt").exists()


def test_poll_does_not_return_the_same_file_twice(tmp_path: Path) -> None:
    watch_dir = tmp_path / "watch"
    storage_dir = tmp_path / "storage"
    watch_dir.mkdir()
    (watch_dir / "invoice.pdf").write_bytes(b"%PDF-1.4 fake")

    source = FolderSource(str(watch_dir), str(storage_dir))
    first = source.poll()
    second = source.poll()

    assert len(first) == 1
    assert len(second) == 0
```

```python
from pathlib import Path

from pipeline.intake.upload_source import UploadSource


def test_save_writes_file_and_returns_document(tmp_path: Path) -> None:
    storage_dir = tmp_path / "storage"
    source = UploadSource(str(storage_dir))

    document = source.save("scan.png", b"fake png bytes")

    assert document.source == "upload"
    assert document.filename == "scan.png"
    assert Path(document.content_path).read_bytes() == b"fake png bytes"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_intake_folder.py tests/test_intake_upload.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pipeline.intake'`.

- [ ] **Step 3: Write pipeline/intake/__init__.py (empty) and pipeline/intake/base.py**

```python
from typing import Protocol

from pipeline.schemas import IncomingDocument


class IntakeSource(Protocol):
    def poll(self) -> list[IncomingDocument]: ...
```

- [ ] **Step 4: Write pipeline/intake/folder_source.py**

```python
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from pipeline.schemas import IncomingDocument

SUPPORTED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg"}


class FolderSource:
    def __init__(self, watch_dir: str, storage_dir: str) -> None:
        self._watch_dir = Path(watch_dir)
        self._storage_dir = Path(storage_dir)
        self._watch_dir.mkdir(parents=True, exist_ok=True)
        self._storage_dir.mkdir(parents=True, exist_ok=True)

    def poll(self) -> list[IncomingDocument]:
        documents: list[IncomingDocument] = []
        for path in sorted(self._watch_dir.iterdir()):
            if not path.is_file() or path.suffix.lower() not in SUPPORTED_EXTENSIONS:
                continue
            doc_id = str(uuid.uuid4())
            destination = self._storage_dir / f"{doc_id}{path.suffix.lower()}"
            shutil.move(str(path), str(destination))
            documents.append(
                IncomingDocument(
                    doc_id=doc_id,
                    source="folder",
                    received_at=datetime.now(timezone.utc),
                    sender=None,
                    filename=path.name,
                    content_path=str(destination),
                )
            )
        return documents
```

- [ ] **Step 5: Write pipeline/intake/upload_source.py**

```python
import uuid
from datetime import datetime, timezone
from pathlib import Path

from pipeline.schemas import IncomingDocument


class UploadSource:
    def __init__(self, storage_dir: str) -> None:
        self._storage_dir = Path(storage_dir)
        self._storage_dir.mkdir(parents=True, exist_ok=True)

    def save(self, filename: str, content: bytes) -> IncomingDocument:
        doc_id = str(uuid.uuid4())
        suffix = Path(filename).suffix.lower()
        destination = self._storage_dir / f"{doc_id}{suffix}"
        destination.write_bytes(content)
        return IncomingDocument(
            doc_id=doc_id,
            source="upload",
            received_at=datetime.now(timezone.utc),
            sender=None,
            filename=filename,
            content_path=str(destination),
        )
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_intake_folder.py tests/test_intake_upload.py -v`
Expected: PASS (3 tests).

- [ ] **Step 7: Commit**

```bash
git add pipeline/intake tests/test_intake_folder.py tests/test_intake_upload.py
git commit -m "feat: folder and upload intake sources"
```

---

### Task 14: IMAP intake source (real Gmail poll)

**Files:**
- Create: `pipeline/intake/imap_source.py`
- Test: `tests/test_intake_imap.py`

**Interfaces:**
- Consumes: `pipeline.schemas.IncomingDocument`, stdlib `imaplib`/`email`.
- Produces: `ImapClient` Protocol (`select`, `search_unseen`, `fetch_message`, `mark_seen`); `RealImapClient` implementing it over `imaplib.IMAP4_SSL`; `ImapSource(client: ImapClient, storage_dir: str, mailbox: str = "INBOX")` implementing the same `poll(self) -> list[IncomingDocument]` contract as `FolderSource`/`UploadSource` — the graph and web server treat all three identically. Used by the web server's background poll loop (Task 15).

- [ ] **Step 1: Write the failing test**

```python
from email.message import EmailMessage
from pathlib import Path

from pipeline.intake.imap_source import ImapSource


def _build_raw_email_with_pdf_attachment() -> bytes:
    msg = EmailMessage()
    msg["From"] = "vendor@example.com"
    msg["Subject"] = "Invoice attached"
    msg.set_content("Please find the invoice attached.")
    msg.add_attachment(b"%PDF-1.4 fake content", maintype="application", subtype="pdf", filename="invoice.pdf")
    return bytes(msg)


class FakeImapClient:
    def __init__(self, raw_messages: dict[bytes, bytes]) -> None:
        self._raw_messages = raw_messages
        self.marked_seen: list[bytes] = []
        self.selected_mailbox: str | None = None

    def select(self, mailbox: str) -> None:
        self.selected_mailbox = mailbox

    def search_unseen(self) -> list[bytes]:
        return list(self._raw_messages.keys())

    def fetch_message(self, msg_id: bytes) -> bytes:
        return self._raw_messages[msg_id]

    def mark_seen(self, msg_id: bytes) -> None:
        self.marked_seen.append(msg_id)


def test_poll_extracts_pdf_attachment_and_marks_seen(tmp_path: Path) -> None:
    client = FakeImapClient({b"1": _build_raw_email_with_pdf_attachment()})
    source = ImapSource(client, str(tmp_path / "storage"))

    documents = source.poll()

    assert len(documents) == 1
    assert documents[0].source == "imap"
    assert documents[0].filename == "invoice.pdf"
    assert documents[0].sender == "vendor@example.com"
    assert Path(documents[0].content_path).read_bytes().startswith(b"%PDF")
    assert client.selected_mailbox == "INBOX"
    assert client.marked_seen == [b"1"]


def test_poll_ignores_messages_without_supported_attachments(tmp_path: Path) -> None:
    msg = EmailMessage()
    msg["From"] = "spam@example.com"
    msg.set_content("no attachment here")
    client = FakeImapClient({b"2": bytes(msg)})
    source = ImapSource(client, str(tmp_path / "storage"))

    documents = source.poll()

    assert documents == []
    assert client.marked_seen == [b"2"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_intake_imap.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pipeline.intake.imap_source'`.

- [ ] **Step 3: Write pipeline/intake/imap_source.py**

```python
import email
import imaplib
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from pipeline.schemas import IncomingDocument

SUPPORTED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg"}


class ImapClient(Protocol):
    def select(self, mailbox: str) -> None: ...
    def search_unseen(self) -> list[bytes]: ...
    def fetch_message(self, msg_id: bytes) -> bytes: ...
    def mark_seen(self, msg_id: bytes) -> None: ...


class RealImapClient:
    def __init__(self, host: str, username: str, app_password: str) -> None:
        self._conn = imaplib.IMAP4_SSL(host)
        status, _ = self._conn.login(username, app_password)
        if status != "OK":
            raise RuntimeError(f"IMAP login failed for {username}@{host}: {status}")

    def select(self, mailbox: str) -> None:
        status, _ = self._conn.select(mailbox)
        if status != "OK":
            raise RuntimeError(f"IMAP select failed for mailbox {mailbox}: {status}")

    def search_unseen(self) -> list[bytes]:
        status, data = self._conn.search(None, "UNSEEN")
        if status != "OK":
            raise RuntimeError(f"IMAP search failed: {status}")
        return data[0].split()

    def fetch_message(self, msg_id: bytes) -> bytes:
        status, data = self._conn.fetch(msg_id, "(RFC822)")
        if status != "OK":
            raise RuntimeError(f"IMAP fetch failed for message {msg_id!r}: {status}")
        return data[0][1]

    def mark_seen(self, msg_id: bytes) -> None:
        self._conn.store(msg_id, "+FLAGS", "\\Seen")


class ImapSource:
    def __init__(self, client: ImapClient, storage_dir: str, mailbox: str = "INBOX") -> None:
        self._client = client
        self._storage_dir = Path(storage_dir)
        self._storage_dir.mkdir(parents=True, exist_ok=True)
        self._mailbox = mailbox

    def poll(self) -> list[IncomingDocument]:
        self._client.select(self._mailbox)
        documents: list[IncomingDocument] = []
        for msg_id in self._client.search_unseen():
            raw_bytes = self._client.fetch_message(msg_id)
            message = email.message_from_bytes(raw_bytes)
            sender = message.get("From")
            for part in message.walk():
                filename = part.get_filename()
                if not filename or Path(filename).suffix.lower() not in SUPPORTED_EXTENSIONS:
                    continue
                payload = part.get_payload(decode=True)
                if not payload:
                    continue
                documents.append(self._save_attachment(sender, filename, payload))
            self._client.mark_seen(msg_id)
        return documents

    def _save_attachment(self, sender: str | None, filename: str, payload: bytes) -> IncomingDocument:
        doc_id = str(uuid.uuid4())
        suffix = Path(filename).suffix.lower()
        destination = self._storage_dir / f"{doc_id}{suffix}"
        destination.write_bytes(payload)
        return IncomingDocument(
            doc_id=doc_id,
            source="imap",
            received_at=datetime.now(timezone.utc),
            sender=sender,
            filename=filename,
            content_path=str(destination),
        )
```

`RealImapClient` is not covered by the automated suite — it needs a live Gmail connection. Step 5 below is the manual check.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_intake_imap.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Manual smoke test against real Gmail**

Set `GMAIL_IMAP_USER` and `GMAIL_IMAP_APP_PASSWORD` in `.env` (a Gmail App Password, not the account password — requires 2-Step Verification enabled on the account; generate one at Google Account → Security → App Passwords). Run:
```bash
python -c "
import os
from dotenv import load_dotenv
load_dotenv()
from pipeline.intake.imap_source import RealImapClient, ImapSource
client = RealImapClient(os.environ['GMAIL_IMAP_HOST'], os.environ['GMAIL_IMAP_USER'], os.environ['GMAIL_IMAP_APP_PASSWORD'])
source = ImapSource(client, 'runs/imap_intake')
docs = source.poll()
print(f'found {len(docs)} document(s)')
for d in docs:
    print(d.filename, d.sender, d.content_path)
"
```
Expected: connects without error and prints 0 or more documents. Send a test email with a PDF attachment to that inbox first, then re-run to confirm it's picked up and marked seen (re-running immediately after should print 0, since it's no longer unseen).

- [ ] **Step 6: Commit**

```bash
git add pipeline/intake/imap_source.py tests/test_intake_imap.py
git commit -m "feat: real Gmail IMAP intake source"
```

---

### Task 15: event bus + FastAPI web server

**Files:**
- Create: `pipeline/events.py`
- Create: `web/__init__.py`
- Create: `web/server.py`
- Test: `tests/test_events.py`
- Test: `tests/test_web_server.py`

**Interfaces:**
- Consumes: `pipeline.graph.{build_graph, start_run, resume_run}`, `pipeline.storage.Repository`, `pipeline.intake.{folder_source.FolderSource, upload_source.UploadSource, imap_source.ImapSource}`.
- Produces: `EventBus(loop: asyncio.AbstractEventLoop)` with `publish(self, run_id: str, stage: str, payload: dict) -> None` and `async subscribe(self, run_id: str) -> AsyncIterator[dict]`; `create_app(repo, extraction_client, triage_client, folder_source, upload_source, checkpoint_db_path, imap_source=None, poll_interval_seconds=5.0) -> FastAPI` — dependencies are injected so tests use fakes and only `if __name__ == "__main__"` wires real ones. Used by Task 16 (UI, calling these HTTP/SSE endpoints) and Task 19 (`__main__` entrypoint).

- [ ] **Step 1: Write the failing test for the event bus**

```python
import asyncio

from pipeline.events import EventBus


def test_publish_then_subscribe_receives_events_and_stops_at_done() -> None:
    async def scenario() -> list[dict]:
        loop = asyncio.get_running_loop()
        bus = EventBus(loop)
        bus.publish("run-1", "triage", {"ok": True})
        bus.publish("run-1", "__done__", {})
        received = []
        async for event in bus.subscribe("run-1"):
            received.append(event)
        return received

    received = asyncio.run(scenario())
    assert received[0] == {"stage": "triage", "payload": {"ok": True}}
    assert received[-1]["stage"] == "__done__"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_events.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pipeline.events'`.

- [ ] **Step 3: Write pipeline/events.py**

```python
import asyncio
from typing import AsyncIterator


class EventBus:
    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._queues: dict[str, asyncio.Queue] = {}

    def _queue_for(self, run_id: str) -> asyncio.Queue:
        if run_id not in self._queues:
            self._queues[run_id] = asyncio.Queue()
        return self._queues[run_id]

    def publish(self, run_id: str, stage: str, payload: dict) -> None:
        queue = self._queue_for(run_id)
        self._loop.call_soon_threadsafe(queue.put_nowait, {"stage": stage, "payload": payload})

    async def subscribe(self, run_id: str) -> AsyncIterator[dict]:
        queue = self._queue_for(run_id)
        while True:
            event = await queue.get()
            yield event
            if event["stage"] == "__done__":
                break
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_events.py -v`
Expected: PASS (1 test).

- [ ] **Step 5: Write the failing test for the web server**

```python
import time
from pathlib import Path

from fastapi.testclient import TestClient

from pipeline.intake.folder_source import FolderSource
from pipeline.intake.upload_source import UploadSource
from pipeline.storage import Repository
from web.server import create_app

FIXTURE = Path(__file__).resolve().parent.parent / "data" / "invoices" / "happy_path.pdf"


class FakeExtractionClient:
    def structure_from_text(self, text: str) -> dict:
        return {"invoice_number": "INV-3001", "invoice_date": "2026-08-02", "vendor_name": "Acme Corp", "po_reference": "PO-1001", "line_items": [], "subtotal": 5000, "tax_amount": 0, "total_amount": 5000, "currency": "USD"}

    def structure_from_image(self, image_bytes: bytes, media_type: str) -> dict:
        raise AssertionError("not expected for this fixture")


class FakeTriageClient:
    def triage_text(self, text: str) -> dict:
        return {"looks_like_invoice": True, "preview_text": "Invoice from Acme Corp", "reason": "has invoice number and total"}

    def triage_image(self, image_bytes: bytes, media_type: str) -> dict:
        raise AssertionError("not expected for this fixture")


def _seeded_app(tmp_path: Path) -> tuple[TestClient, Repository]:
    from datetime import date
    from decimal import Decimal

    from pipeline.schemas import PurchaseOrder, VendorRecord

    repo = Repository(str(tmp_path / "app.db"))
    repo.init_db()
    repo.seed_reference_data(
        [VendorRecord(vendor_id="V-ACME", canonical_name="Acme Corporation Pvt Ltd", aliases=["Acme Corp"], approved=True)],
        [PurchaseOrder(po_id="PO-1001", vendor_id="V-ACME", amount=Decimal("5000"), issued_date=date(2026, 8, 1), tax_treatment="exclusive")],
    )
    folder_source = FolderSource(str(tmp_path / "inbox"), str(tmp_path / "intake_storage"))
    upload_source = UploadSource(str(tmp_path / "uploads"))
    app = create_app(
        repo,
        FakeExtractionClient(),
        FakeTriageClient(),
        folder_source,
        upload_source,
        str(tmp_path / "checkpoints.db"),
        poll_interval_seconds=999.0,
    )
    return TestClient(app), repo


def test_upload_triages_and_appears_in_gate_pending(tmp_path: Path) -> None:
    with _seeded_app(tmp_path)[0] as client:
        with open(FIXTURE, "rb") as f:
            response = client.post("/api/upload", files={"file": ("happy_path.pdf", f, "application/pdf")})
        assert response.status_code == 200
        run_id = response.json()["run_id"]

        pending = client.get("/api/gate/pending").json()
        assert any(p["run_id"] == run_id for p in pending)
        matching = next(p for p in pending if p["run_id"] == run_id)
        assert matching["triage"]["looks_like_invoice"] is True


def test_approve_runs_pipeline_to_auto_approve(tmp_path: Path) -> None:
    with _seeded_app(tmp_path)[0] as client:
        with open(FIXTURE, "rb") as f:
            response = client.post("/api/upload", files={"file": ("happy_path.pdf", f, "application/pdf")})
        run_id = response.json()["run_id"]

        approve_response = client.post(f"/api/gate/{run_id}/approve")
        assert approve_response.status_code == 200

        final_status = None
        for _ in range(40):
            run = client.get(f"/api/runs/{run_id}").json()
            final_status = run["status"]
            if final_status == "DONE":
                assert run["decision"]["outcome"] == "AUTO_APPROVE"
                break
            time.sleep(0.05)
        assert final_status == "DONE"


def test_reject_marks_run_rejected(tmp_path: Path) -> None:
    with _seeded_app(tmp_path)[0] as client:
        with open(FIXTURE, "rb") as f:
            response = client.post("/api/upload", files={"file": ("happy_path.pdf", f, "application/pdf")})
        run_id = response.json()["run_id"]

        client.post(f"/api/gate/{run_id}/reject")

        final_status = None
        for _ in range(40):
            run = client.get(f"/api/runs/{run_id}").json()
            final_status = run["status"]
            if final_status == "REJECTED_AT_GATE":
                break
            time.sleep(0.05)
        assert final_status == "REJECTED_AT_GATE"
```

- [ ] **Step 6: Run test to verify it fails**

Run: `pytest tests/test_web_server.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'web.server'`.

- [ ] **Step 7: Write web/__init__.py (empty) and web/server.py**

```python
import asyncio
import json
import logging
import uuid
from typing import Any

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

from pipeline.events import EventBus
from pipeline.graph import build_graph, resume_run, start_run
from pipeline.intake.folder_source import FolderSource
from pipeline.intake.imap_source import ImapSource
from pipeline.intake.upload_source import UploadSource
from pipeline.storage import Repository

logger = logging.getLogger("invoice_pipeline.web")


def create_app(
    repo: Repository,
    extraction_client: Any,
    triage_client: Any,
    folder_source: FolderSource,
    upload_source: UploadSource,
    checkpoint_db_path: str,
    imap_source: ImapSource | None = None,
    poll_interval_seconds: float = 5.0,
) -> FastAPI:
    app = FastAPI()

    async def run_resume_safely(run_id: str, approved: bool) -> None:
        try:
            await asyncio.to_thread(resume_run, app.state.graph, run_id, approved)
        except Exception as exc:
            logger.exception(f"pipeline run {run_id} failed after gate decision (approved={approved}): {exc}")
            repo.update_run_status(run_id, "ERROR")

    async def run_start_safely(run_id: str, document: Any) -> None:
        try:
            await asyncio.to_thread(start_run, app.state.graph, run_id, document, repo)
        except Exception as exc:
            logger.exception(f"pipeline run {run_id} failed during intake/triage: {exc}")
            repo.update_run_status(run_id, "ERROR")

    async def poll_loop() -> None:
        while True:
            try:
                documents = folder_source.poll()
                if imap_source is not None:
                    documents = documents + imap_source.poll()
                for document in documents:
                    run_id = str(uuid.uuid4())
                    await run_start_safely(run_id, document)
            except Exception as exc:
                logger.exception(f"intake poll loop failed: {exc}")
            await asyncio.sleep(poll_interval_seconds)

    @app.on_event("startup")
    async def startup() -> None:
        loop = asyncio.get_running_loop()
        app.state.bus = EventBus(loop)
        app.state.graph = build_graph(repo, extraction_client, triage_client, checkpoint_db_path, on_stage=app.state.bus.publish)
        app.state.poll_task = asyncio.create_task(poll_loop())

    @app.on_event("shutdown")
    async def shutdown() -> None:
        app.state.poll_task.cancel()

    @app.post("/api/upload")
    async def upload(file: UploadFile) -> dict:
        content = await file.read()
        document = upload_source.save(file.filename, content)
        run_id = str(uuid.uuid4())
        await run_start_safely(run_id, document)
        return {"run_id": run_id, "doc_id": document.doc_id}

    @app.get("/api/gate/pending")
    def gate_pending() -> list[dict]:
        pending = [run for run in repo.list_runs() if run["status"] == "AWAITING_APPROVAL"]
        result = []
        for run in pending:
            stages = repo.get_stages(run["run_id"])
            triage_stage = next((s for s in stages if s["stage"] == "triage"), None)
            result.append({**run, "triage": triage_stage["payload"] if triage_stage else None})
        return result

    @app.post("/api/gate/{run_id}/approve")
    async def approve(run_id: str) -> dict:
        asyncio.create_task(run_resume_safely(run_id, True))
        return {"status": "started"}

    @app.post("/api/gate/{run_id}/reject")
    async def reject(run_id: str) -> dict:
        asyncio.create_task(run_resume_safely(run_id, False))
        return {"status": "started"}

    @app.get("/api/runs")
    def list_runs() -> list[dict]:
        return repo.list_runs()

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str) -> dict:
        run = repo.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"run {run_id} not found")
        return {**run, "stages": repo.get_stages(run_id)}

    @app.get("/api/runs/{run_id}/events")
    async def run_events(run_id: str) -> StreamingResponse:
        async def event_stream():
            async for event in app.state.bus.subscribe(run_id):
                yield f"data: {json.dumps(event)}\n\n"

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    app.mount("/", StaticFiles(directory="web/static", html=True), name="static")
    return app
```

- [ ] **Step 8: Run test to verify it passes**

Run: `pytest tests/test_web_server.py -v`
Expected: PASS (3 tests). `web/server.py` mounts `web/static/` at `/`, which does not exist until Task 16 — create an empty `web/static/index.html` placeholder now (`mkdir -p web/static && echo "placeholder" > web/static/index.html`, or the Windows equivalent) so the app can start; Task 16 replaces it with the real UI.

- [ ] **Step 9: Commit**

```bash
git add pipeline/events.py web tests/test_events.py tests/test_web_server.py
git commit -m "feat: FastAPI web server with SSE event streaming"
```

---

### Task 16: UI — Gate, Live Run, Dashboard

**Files:**
- Create: `web/static/index.html` (replaces the Task 15 placeholder)
- Create: `web/static/styles.css`
- Create: `web/static/app.js`
- Test: `tests/test_static_assets.py`

**Interfaces:**
- Consumes: the endpoints from Task 15 (`/api/upload`, `/api/gate/pending`, `/api/gate/{run_id}/approve`, `/api/gate/{run_id}/reject`, `/api/runs`, `/api/runs/{run_id}`, `/api/runs/{run_id}/events`).
- Produces: the three-tab SPA the case study grades directly — Gate (pending approvals + upload), Live Run (SSE stage timeline), Dashboard (run history table). No JS framework; vanilla DOM + `EventSource`.

This is UI code, not business logic — there's no meaningful unit test for "does clicking a button call fetch." Step 4's automated test only guards against the most common real bug (a broken asset reference), and Step 5 is a manual click-through checklist. If there's time left after Task 19, consider a design pass here with the `frontend-design` skill — the case study explicitly grades UI quality, and this version is functional but plain.

- [ ] **Step 1: Write web/static/index.html**

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Invoice Decision Pipeline</title>
<link rel="stylesheet" href="/styles.css">
</head>
<body>
<header class="topbar">
  <h1>Invoice Decision Pipeline</h1>
  <nav class="tabs">
    <button class="tab-button active" data-tab="gate">Gate</button>
    <button class="tab-button" data-tab="live">Live Run</button>
    <button class="tab-button" data-tab="dashboard">Dashboard</button>
  </nav>
</header>
<main>
  <section id="tab-gate" class="tab-panel active">
    <div class="upload-box">
      <span class="upload-label">Drop an invoice here or click to upload</span>
      <input id="upload-input" type="file" accept=".pdf,.png,.jpg,.jpeg">
    </div>
    <div id="gate-list" class="card-list"></div>
  </section>
  <section id="tab-live" class="tab-panel">
    <div class="live-controls">
      <label for="run-select">Run:</label>
      <select id="run-select"></select>
    </div>
    <ol id="stage-timeline" class="stage-timeline"></ol>
  </section>
  <section id="tab-dashboard" class="tab-panel">
    <table id="runs-table" class="runs-table">
      <thead>
        <tr><th>Run</th><th>Status</th><th>Decision</th><th>Reasons</th><th>Updated</th></tr>
      </thead>
      <tbody></tbody>
    </table>
  </section>
</main>
<script src="/app.js"></script>
</body>
</html>
```

- [ ] **Step 2: Write web/static/styles.css**

```css
:root {
  --bg: #0f1115;
  --panel: #171a21;
  --border: #262b36;
  --text: #e6e8ec;
  --muted: #9aa2b1;
  --accent: #5b8cff;
  --approve: #35c46b;
  --review: #e8a83c;
  --reject: #e0524d;
}

* { box-sizing: border-box; }

body {
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font-family: -apple-system, "Segoe UI", Roboto, sans-serif;
}

.topbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 16px 24px;
  border-bottom: 1px solid var(--border);
}

.tabs { display: flex; gap: 8px; }

.tab-button {
  background: transparent;
  color: var(--muted);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 8px 16px;
  cursor: pointer;
}

.tab-button.active { color: var(--text); border-color: var(--accent); }

main { padding: 24px; max-width: 1000px; margin: 0 auto; }

.tab-panel { display: none; }
.tab-panel.active { display: block; }

.upload-box {
  border: 2px dashed var(--border);
  border-radius: 10px;
  padding: 32px;
  text-align: center;
  margin-bottom: 24px;
  cursor: pointer;
}

.upload-label { color: var(--muted); }

#upload-input { display: none; }

.card-list { display: flex; flex-direction: column; gap: 12px; }

.gate-card {
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 16px;
}

.gate-card-header { display: flex; justify-content: space-between; }

.gate-card-actions { display: flex; gap: 8px; margin-top: 12px; }

button.approve, button.reject {
  border: none;
  font-weight: 600;
  padding: 8px 16px;
  border-radius: 6px;
  cursor: pointer;
}
button.approve { background: var(--approve); color: #08110c; }
button.reject { background: var(--reject); color: #2a0908; }

.live-controls { margin-bottom: 16px; display: flex; gap: 8px; align-items: center; }

#run-select { background: var(--panel); color: var(--text); border: 1px solid var(--border); padding: 6px; border-radius: 6px; }

.stage-timeline { list-style: none; padding: 0; display: flex; flex-direction: column; gap: 8px; }

.stage-item {
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 12px 16px;
  cursor: pointer;
}

.stage-item-header { display: flex; justify-content: space-between; align-items: center; }

.stage-payload {
  display: none;
  white-space: pre-wrap;
  font-family: "SFMono-Regular", Consolas, monospace;
  font-size: 13px;
  margin-top: 8px;
  color: var(--muted);
}

.stage-item.expanded .stage-payload { display: block; }

.runs-table { width: 100%; border-collapse: collapse; }

.runs-table th, .runs-table td {
  text-align: left;
  padding: 10px 12px;
  border-bottom: 1px solid var(--border);
}

.runs-table tbody tr { cursor: pointer; }

.badge { padding: 2px 10px; border-radius: 999px; font-size: 12px; font-weight: 600; }
.badge.AUTO_APPROVE { background: rgba(53,196,107,0.15); color: var(--approve); }
.badge.NEEDS_REVIEW { background: rgba(232,168,60,0.15); color: var(--review); }
.badge.REJECT { background: rgba(224,82,77,0.15); color: var(--reject); }
.badge.AWAITING_APPROVAL, .badge.RUNNING { background: rgba(91,140,255,0.15); color: var(--accent); }
```

- [ ] **Step 3: Write web/static/app.js**

```javascript
function activateTab(name) {
  document.querySelectorAll(".tab-button").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.tab === name);
  });
  document.querySelectorAll(".tab-panel").forEach((panel) => {
    panel.classList.toggle("active", panel.id === `tab-${name}`);
  });
  if (name === "dashboard") refreshDashboard();
  if (name === "gate") refreshGate();
  if (name === "live") refreshRunSelect();
}

document.querySelectorAll(".tab-button").forEach((btn) => {
  btn.addEventListener("click", () => activateTab(btn.dataset.tab));
});

const uploadBox = document.querySelector(".upload-box");
const uploadInput = document.getElementById("upload-input");

uploadBox.addEventListener("click", () => uploadInput.click());
uploadInput.addEventListener("change", async () => {
  if (!uploadInput.files.length) return;
  await uploadFile(uploadInput.files[0]);
  uploadInput.value = "";
});
uploadBox.addEventListener("dragover", (event) => event.preventDefault());
uploadBox.addEventListener("drop", async (event) => {
  event.preventDefault();
  if (event.dataTransfer.files.length) {
    await uploadFile(event.dataTransfer.files[0]);
  }
});

async function uploadFile(file) {
  const formData = new FormData();
  formData.append("file", file);
  await fetch("/api/upload", { method: "POST", body: formData });
  refreshGate();
}

async function refreshGate() {
  const response = await fetch("/api/gate/pending");
  const pending = await response.json();
  const container = document.getElementById("gate-list");
  container.innerHTML = "";
  pending.forEach((run) => {
    const card = document.createElement("div");
    card.className = "gate-card";
    const preview = run.triage ? run.triage.preview_text : "no preview available";
    card.innerHTML = `
      <div class="gate-card-header">
        <strong>${run.doc_id}</strong>
        <span class="badge ${run.status}">${run.status}</span>
      </div>
      <p>${preview}</p>
      <div class="gate-card-actions">
        <button class="approve" data-run="${run.run_id}">Approve</button>
        <button class="reject" data-run="${run.run_id}">Reject</button>
      </div>
    `;
    container.appendChild(card);
  });
  container.querySelectorAll("button.approve").forEach((btn) => {
    btn.addEventListener("click", () => decide(btn.dataset.run, "approve"));
  });
  container.querySelectorAll("button.reject").forEach((btn) => {
    btn.addEventListener("click", () => decide(btn.dataset.run, "reject"));
  });
}

async function decide(runId, action) {
  await fetch(`/api/gate/${runId}/${action}`, { method: "POST" });
  refreshGate();
  activateTab("live");
  watchRun(runId);
}

async function refreshRunSelect() {
  const response = await fetch("/api/runs");
  const runs = await response.json();
  const select = document.getElementById("run-select");
  select.innerHTML = "";
  runs.forEach((run) => {
    const option = document.createElement("option");
    option.value = run.run_id;
    option.textContent = `${run.run_id} — ${run.status}`;
    select.appendChild(option);
  });
  select.onchange = () => watchRun(select.value);
  if (runs.length) watchRun(runs[0].run_id);
}

let activeEventSource = null;

function watchRun(runId) {
  if (activeEventSource) activeEventSource.close();
  const timeline = document.getElementById("stage-timeline");
  timeline.innerHTML = "";
  loadExistingStages(runId);
  activeEventSource = new EventSource(`/api/runs/${runId}/events`);
  activeEventSource.onmessage = (event) => {
    const data = JSON.parse(event.data);
    if (data.stage === "__done__") {
      activeEventSource.close();
      return;
    }
    appendStage(data.stage, data.payload);
  };
}

async function loadExistingStages(runId) {
  const response = await fetch(`/api/runs/${runId}`);
  const run = await response.json();
  run.stages.forEach((stage) => appendStage(stage.stage, stage.payload));
}

function appendStage(stage, payload) {
  const timeline = document.getElementById("stage-timeline");
  const item = document.createElement("li");
  item.className = "stage-item";
  item.innerHTML = `
    <div class="stage-item-header">
      <span>${stage}</span>
      <span>expand</span>
    </div>
    <pre class="stage-payload">${JSON.stringify(payload, null, 2)}</pre>
  `;
  item.addEventListener("click", () => item.classList.toggle("expanded"));
  timeline.appendChild(item);
}

async function refreshDashboard() {
  const response = await fetch("/api/runs");
  const runs = await response.json();
  const tbody = document.querySelector("#runs-table tbody");
  tbody.innerHTML = "";
  runs.forEach((run) => {
    const row = document.createElement("tr");
    const decision = run.decision;
    const outcome = decision ? decision.outcome : "-";
    const reasons = decision ? decision.reason_codes.join(", ") : "-";
    row.innerHTML = `
      <td>${run.run_id}</td>
      <td><span class="badge ${run.status}">${run.status}</span></td>
      <td>${outcome !== "-" ? `<span class="badge ${outcome}">${outcome}</span>` : "-"}</td>
      <td>${reasons}</td>
      <td>${run.updated_at}</td>
    `;
    row.addEventListener("click", () => {
      activateTab("live");
      watchRun(run.run_id);
    });
    tbody.appendChild(row);
  });
}

refreshGate();
```

- [ ] **Step 4: Write and run the asset sanity test**

```python
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parent.parent / "web" / "static"


def test_index_references_existing_static_files() -> None:
    html = (STATIC_DIR / "index.html").read_text()
    assert "/styles.css" in html
    assert "/app.js" in html
    assert (STATIC_DIR / "styles.css").exists()
    assert (STATIC_DIR / "app.js").exists()


def test_app_js_calls_every_server_endpoint_from_task_15() -> None:
    js = (STATIC_DIR / "app.js").read_text()
    for endpoint in ["/api/upload", "/api/gate/pending", "/api/runs", "/events"]:
        assert endpoint in js
```

Run: `pytest tests/test_static_assets.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Manual click-through checklist**

Run `python -m web.server` (built in Task 19) and in a browser: (1) drag `data/invoices/happy_path.pdf` onto the upload box, confirm it appears under Gate with a preview; (2) click Approve, confirm the view switches to Live Run and stages appear one at a time (triage already there, then gate/extract/normalize/validate/vendor_resolve/po_match/decide streaming in); (3) open Dashboard, confirm the run shows `AUTO_APPROVE` with a green badge; (4) click that row, confirm it reopens the same stage timeline.

- [ ] **Step 6: Commit**

```bash
git add web/static tests/test_static_assets.py
git commit -m "feat: gate, live run, and dashboard UI"
```

---

### Task 17: rerun CLI — replay any pure stage from saved input

**Files:**
- Create: `pipeline/rerun.py`
- Test: `tests/test_rerun.py`

**Interfaces:**
- Consumes: `pipeline.storage.Repository`, `pipeline.{normalize,validate,vendor_resolve,po_match,decision}`.
- Produces: `rerun_stage(repo: Repository, run_id: str, stage: str) -> dict` and a `python -m pipeline.rerun --run <id> --from <stage>` CLI entrypoint. Covers the global constraint that every pipeline stage must be independently re-runnable from saved input — reconstructs each rerunnable stage's input from the *previous* stage's persisted JSON (not from a cached copy of its own prior output), so a config or rule change is actually re-evaluated, not replayed.

- [ ] **Step 1: Write the failing test**

```python
from datetime import date
from decimal import Decimal

from pipeline.rerun import rerun_stage
from pipeline.schemas import Invoice, MatchResult, PurchaseOrder, VendorRecord
from pipeline.storage import Repository


def _repo(tmp_path) -> Repository:
    repo = Repository(str(tmp_path / "test.db"))
    repo.init_db()
    return repo


def test_rerun_normalize_validate_vendor_resolve_chain(tmp_path) -> None:
    repo = _repo(tmp_path)
    vendor = VendorRecord(vendor_id="V-ACME", canonical_name="Acme Corporation Pvt Ltd", aliases=["Acme Corp"], approved=True)
    repo.seed_reference_data([vendor], [])
    repo.create_run("run-3", "doc-3", "RUNNING")
    repo.append_stage("run-3", "extract", {
        "raw": {"invoice_number": "INV-3", "invoice_date": "2026-08-02", "vendor_name": "Acme Corp", "line_items": [], "subtotal": 100, "tax_amount": 0, "total_amount": 100, "currency": "USD"},
        "extraction_path": "pdf_text",
        "confidence": 0.9,
    })

    normalized = rerun_stage(repo, "run-3", "normalize")
    assert normalized["invoice_number"] == "INV-3"

    repo.append_stage("run-3", "normalize", normalized)
    validated = rerun_stage(repo, "run-3", "validate")
    assert validated["missing_fields"] == []

    repo.append_stage("run-3", "validate", validated)
    resolved = rerun_stage(repo, "run-3", "vendor_resolve")
    assert resolved["vendor"]["vendor_id"] == "V-ACME"


def test_rerun_po_match_reflects_current_po_state_not_a_cached_copy(tmp_path) -> None:
    repo = _repo(tmp_path)
    vendor = VendorRecord(vendor_id="V-ACME", canonical_name="Acme Corporation Pvt Ltd", aliases=["Acme Corp"], approved=True)
    po = PurchaseOrder(po_id="PO-1001", vendor_id="V-ACME", amount=Decimal("5000"), issued_date=date(2026, 8, 1), tax_treatment="exclusive")
    repo.seed_reference_data([vendor], [po])

    invoice = Invoice(
        invoice_number="INV-1", invoice_date=date(2026, 8, 2), vendor_name_raw="Acme Corp", po_reference="PO-1001",
        line_items=[], subtotal=Decimal("5400"), tax_amount=Decimal("0"), total_amount=Decimal("5400"),
        extraction_path="pdf_text", extraction_confidence=0.95,
    )
    repo.create_run("run-1", "doc-1", "RUNNING")
    repo.append_stage("run-1", "extract", {"raw": {}, "extraction_path": "pdf_text", "confidence": 0.95})
    repo.append_stage("run-1", "validate", invoice.model_dump(mode="json"))
    repo.append_stage("run-1", "vendor_resolve", {"vendor": vendor.model_dump()})

    first_match = rerun_stage(repo, "run-1", "po_match")
    assert first_match["amount_within_tolerance"] is False

    repo.seed_reference_data([], [po.model_copy(update={"amount": Decimal("5400")})])
    second_match = rerun_stage(repo, "run-1", "po_match")
    assert second_match["amount_within_tolerance"] is True


def test_rerun_decide_from_persisted_validate_and_match(tmp_path) -> None:
    repo = _repo(tmp_path)
    vendor = VendorRecord(vendor_id="V-ACME", canonical_name="Acme Corporation Pvt Ltd", aliases=[], approved=True)
    repo.seed_reference_data([vendor], [])
    invoice = Invoice(
        invoice_number="INV-2", invoice_date=date(2026, 8, 2), vendor_name_raw="Acme Corp",
        line_items=[], total_amount=Decimal("100"), extraction_path="pdf_text", extraction_confidence=0.95,
    )
    match = MatchResult(vendor=vendor, po=None)
    repo.create_run("run-2", "doc-2", "RUNNING")
    repo.append_stage("run-2", "extract", {"raw": {}, "extraction_path": "pdf_text", "confidence": 0.95})
    repo.append_stage("run-2", "validate", invoice.model_dump(mode="json"))
    repo.append_stage("run-2", "po_match", match.model_dump(mode="json"))

    result = rerun_stage(repo, "run-2", "decide")
    assert result["outcome"] == "NEEDS_REVIEW"
    assert "PO_NOT_FOUND" in result["reason_codes"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_rerun.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pipeline.rerun'`.

- [ ] **Step 3: Write pipeline/rerun.py**

```python
import argparse
import json

from pipeline.decision import decide
from pipeline.normalize import normalize_invoice
from pipeline.po_match import match_po
from pipeline.schemas import Invoice, MatchResult, VendorRecord
from pipeline.storage import Repository
from pipeline.validate import validate_invoice
from pipeline.vendor_resolve import resolve_vendor

RERUNNABLE_STAGES = ["normalize", "validate", "vendor_resolve", "po_match", "decide"]


def rerun_stage(repo: Repository, run_id: str, stage: str) -> dict:
    if stage not in RERUNNABLE_STAGES:
        raise ValueError(f"stage '{stage}' is not rerunnable in isolation; choose one of {RERUNNABLE_STAGES}")

    stages_by_name = {s["stage"]: s["payload"] for s in repo.get_stages(run_id)}

    if stage == "normalize":
        if "extract" not in stages_by_name:
            raise ValueError(f"run {run_id} has no persisted 'extract' stage to rerun normalize from")
        extract_payload = stages_by_name["extract"]
        invoice = normalize_invoice(extract_payload["raw"], extract_payload["extraction_path"], extract_payload["confidence"])
        return invoice.model_dump(mode="json")

    if stage == "validate":
        invoice = Invoice.model_validate(stages_by_name["normalize"])
        return validate_invoice(invoice).model_dump(mode="json")

    if stage == "vendor_resolve":
        invoice = Invoice.model_validate(stages_by_name["validate"])
        vendor = resolve_vendor(invoice.vendor_name_raw, repo.get_vendors())
        return {"vendor": vendor.model_dump() if vendor else None}

    if stage == "po_match":
        invoice = Invoice.model_validate(stages_by_name["validate"])
        vendor_payload = stages_by_name["vendor_resolve"]["vendor"]
        vendor = VendorRecord.model_validate(vendor_payload) if vendor_payload else None
        prior_invoices = repo.get_prior_invoices(vendor.vendor_id, run_id) if vendor is not None else []
        match = match_po(invoice, vendor, repo.get_purchase_orders(), prior_invoices)
        return match.model_dump(mode="json")

    invoice = Invoice.model_validate(stages_by_name["validate"])
    match = MatchResult.model_validate(stages_by_name["po_match"])
    return decide(invoice, match).model_dump(mode="json")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="runs/app.db")
    parser.add_argument("--run", required=True)
    parser.add_argument("--from", dest="stage", required=True, choices=RERUNNABLE_STAGES)
    args = parser.parse_args()

    repo = Repository(args.db)
    result = rerun_stage(repo, args.run, args.stage)
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_rerun.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add pipeline/rerun.py tests/test_rerun.py
git commit -m "feat: rerun CLI for isolated stage replay"
```
