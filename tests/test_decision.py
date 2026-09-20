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
