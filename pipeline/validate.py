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
