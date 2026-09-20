from datetime import date
from decimal import Decimal

from pipeline.schemas import Invoice, PurchaseOrder, VendorRecord
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
