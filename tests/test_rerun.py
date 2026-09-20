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
