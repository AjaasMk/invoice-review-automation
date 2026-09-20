from pipeline.extraction.confidence import compute_confidence


def test_all_critical_fields_present_gives_full_confidence() -> None:
    raw = {"invoice_number": "INV-1", "invoice_date": "2026-08-02", "vendor_name": "Acme", "total_amount": 5000}
    assert compute_confidence(raw) == 1.0


def test_missing_invoice_number_reduces_confidence() -> None:
    raw = {"invoice_number": None, "invoice_date": "2026-08-02", "vendor_name": "Acme", "total_amount": 5000}
    assert compute_confidence(raw) == 0.75


def test_all_missing_gives_zero_confidence() -> None:
    assert compute_confidence({}) == 0.0
