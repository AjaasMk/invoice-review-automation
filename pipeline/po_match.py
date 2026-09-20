from decimal import Decimal

from pipeline.config import (
    AMOUNT_TOLERANCE_MIN,
    AMOUNT_TOLERANCE_PCT,
    DUPLICATE_AMOUNT_TOLERANCE_PCT,
    DUPLICATE_DATE_WINDOW_DAYS,
    PO_DATE_WINDOW_DAYS,
)
from pipeline.schemas import Invoice, MatchEvidence, MatchResult, PurchaseOrder, VendorRecord


def match_po(
    invoice: Invoice,
    vendor: VendorRecord | None,
    candidate_pos: list[PurchaseOrder],
    prior_invoices: list[Invoice],
) -> MatchResult:
    duplicate_of = _find_duplicates(invoice, prior_invoices)

    if vendor is None:
        return MatchResult(
            vendor=None,
            evidence=[MatchEvidence(label="vendor", detail=f"No vendor match for '{invoice.vendor_name_raw}'", weight=0.0)],
            duplicate_of=duplicate_of,
        )

    vendor_pos = [po for po in candidate_pos if po.vendor_id == vendor.vendor_id]
    if not vendor_pos:
        return MatchResult(
            vendor=vendor,
            evidence=[MatchEvidence(label="po_search", detail=f"No purchase orders found for vendor {vendor.canonical_name}", weight=0.0)],
            duplicate_of=duplicate_of,
        )

    scored = [(po, *_score_po(invoice, po)) for po in vendor_pos]
    best_po, best_score, best_evidence = max(scored, key=lambda item: item[1])

    comparison_amount = _comparison_amount(invoice, best_po)
    remaining_before = best_po.amount - best_po.invoiced_to_date
    tolerance = max(best_po.amount * AMOUNT_TOLERANCE_PCT, AMOUNT_TOLERANCE_MIN)
    within_tolerance = comparison_amount is not None and comparison_amount <= remaining_before + tolerance
    remaining_after = remaining_before - comparison_amount if comparison_amount is not None else remaining_before

    return MatchResult(
        po=best_po,
        vendor=vendor,
        score=best_score,
        evidence=best_evidence,
        comparison_amount=comparison_amount,
        amount_within_tolerance=within_tolerance,
        remaining_po_balance=remaining_after,
        duplicate_of=duplicate_of,
    )


def _comparison_amount(invoice: Invoice, po: PurchaseOrder) -> Decimal | None:
    if po.tax_treatment == "inclusive":
        return invoice.total_amount
    return invoice.subtotal if invoice.subtotal is not None else invoice.total_amount


def _score_po(invoice: Invoice, po: PurchaseOrder) -> tuple[float, list[MatchEvidence]]:
    evidence: list[MatchEvidence] = []
    score = 0.0

    if invoice.po_reference and invoice.po_reference.strip().lower() == po.po_id.lower():
        weight = 0.6
        score += weight
        evidence.append(MatchEvidence(label="po_reference", detail=f"Invoice explicitly references {po.po_id}", weight=weight))

    if invoice.invoice_date is not None:
        days = abs((invoice.invoice_date - po.issued_date).days)
        if days <= PO_DATE_WINDOW_DAYS:
            weight = 0.2 * (1 - (days / PO_DATE_WINDOW_DAYS))
            score += weight
            evidence.append(MatchEvidence(label="date_proximity", detail=f"Invoice dated {days} day(s) from PO issue date", weight=weight))

    remaining = po.amount - po.invoiced_to_date
    comparison = _comparison_amount(invoice, po)
    if comparison is not None and remaining > 0:
        closeness = max(Decimal("0"), Decimal("1") - abs(comparison - remaining) / remaining)
        weight = float(closeness) * 0.2
        score += weight
        evidence.append(MatchEvidence(label="amount_proximity", detail=f"Comparison amount {comparison} close to remaining PO balance {remaining}", weight=weight))

    return score, evidence


def _find_duplicates(invoice: Invoice, prior_invoices: list[Invoice]) -> list[str]:
    duplicates: list[str] = []
    if invoice.total_amount is None or invoice.invoice_date is None:
        return duplicates
    for prior in prior_invoices:
        if prior.invoice_number == invoice.invoice_number:
            continue
        if prior.total_amount is None or prior.invoice_date is None or prior.total_amount == 0:
            continue
        amount_diff_pct = abs(invoice.total_amount - prior.total_amount) / prior.total_amount
        if amount_diff_pct > DUPLICATE_AMOUNT_TOLERANCE_PCT:
            continue
        if abs((invoice.invoice_date - prior.invoice_date).days) > DUPLICATE_DATE_WINDOW_DAYS:
            continue
        duplicates.append(prior.invoice_number or "unknown")
    return duplicates
