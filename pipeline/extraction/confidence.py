CRITICAL_FIELDS = ["invoice_number", "invoice_date", "vendor_name", "total_amount"]


def compute_confidence(raw: dict) -> float:
    present = sum(1 for field_name in CRITICAL_FIELDS if raw.get(field_name) not in (None, ""))
    return present / len(CRITICAL_FIELDS)
