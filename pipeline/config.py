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
