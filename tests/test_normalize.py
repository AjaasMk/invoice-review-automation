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
