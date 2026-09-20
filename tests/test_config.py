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
