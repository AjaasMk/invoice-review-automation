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
