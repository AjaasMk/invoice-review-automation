from pipeline.config import EXTRACTION_CONFIDENCE_FLOOR
from pipeline.schemas import Decision, Invoice, MatchResult

_EXPLANATIONS = {
    "LOW_EXTRACTION_CONFIDENCE": "Extraction confidence is below the required floor.",
    "MISSING_REQUIRED_FIELD": "One or more required fields could not be extracted.",
    "VENDOR_NOT_FOUND": "No vendor match was found for the invoice's stated vendor name.",
    "VENDOR_NOT_APPROVED": "The matched vendor is not on the approved vendor list.",
    "DUPLICATE_SUSPECTED": "This invoice closely resembles a prior invoice from the same vendor.",
    "PO_NOT_FOUND": "No matching purchase order was found for this vendor.",
    "AMOUNT_OVER_TOLERANCE": "The invoice amount is outside the allowed tolerance of the purchase order amount.",
    "PO_BALANCE_EXCEEDED": "The invoice amount exceeds the purchase order's remaining balance beyond tolerance.",
}


def decide(invoice: Invoice, match: MatchResult) -> Decision:
    reason_codes: list[str] = []

    if invoice.extraction_confidence < EXTRACTION_CONFIDENCE_FLOOR:
        reason_codes.append("LOW_EXTRACTION_CONFIDENCE")
    if invoice.missing_fields:
        reason_codes.append("MISSING_REQUIRED_FIELD")

    if match.vendor is None:
        reason_codes.append("VENDOR_NOT_FOUND")
    elif not match.vendor.approved:
        return Decision(
            outcome="REJECT",
            reason_codes=["VENDOR_NOT_APPROVED"],
            explanation=_EXPLANATIONS["VENDOR_NOT_APPROVED"],
            evidence=match.evidence,
        )

    if match.duplicate_of:
        reason_codes.append("DUPLICATE_SUSPECTED")

    if match.vendor is not None and match.po is None:
        reason_codes.append("PO_NOT_FOUND")

    if match.po is not None and match.comparison_amount is not None and not match.amount_within_tolerance:
        if match.po.invoiced_to_date > 0:
            reason_codes.append("PO_BALANCE_EXCEEDED")
        else:
            reason_codes.append("AMOUNT_OVER_TOLERANCE")

    if not reason_codes:
        vendor_name = match.vendor.canonical_name if match.vendor else invoice.vendor_name_raw
        po_id = match.po.po_id if match.po else "unknown"
        return Decision(
            outcome="AUTO_APPROVE",
            reason_codes=[],
            explanation=f"Invoice {invoice.invoice_number} from {vendor_name} matches {po_id} within tolerance.",
            evidence=match.evidence,
        )

    explanation = " ".join(_EXPLANATIONS[code] for code in reason_codes)
    return Decision(outcome="NEEDS_REVIEW", reason_codes=reason_codes, explanation=explanation, evidence=match.evidence)
