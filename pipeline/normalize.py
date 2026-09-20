from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from pipeline.schemas import Invoice, LineItem


def normalize_invoice(raw: dict[str, Any], extraction_path: str, confidence: float) -> Invoice:
    line_items = [_to_line_item(item) for item in raw.get("line_items", [])]
    return Invoice(
        invoice_number=raw.get("invoice_number"),
        invoice_date=_to_date(raw.get("invoice_date"), "invoice_date"),
        vendor_name_raw=raw.get("vendor_name") or "",
        po_reference=raw.get("po_reference"),
        line_items=line_items,
        subtotal=_to_decimal(raw.get("subtotal"), "subtotal"),
        tax_amount=_to_decimal(raw.get("tax_amount"), "tax_amount"),
        total_amount=_to_decimal(raw.get("total_amount"), "total_amount"),
        currency=raw.get("currency") or "USD",
        extraction_path=extraction_path,
        extraction_confidence=confidence,
    )


def _to_line_item(item: dict[str, Any]) -> LineItem:
    return LineItem(
        description=item.get("description", ""),
        quantity=_to_decimal(item.get("quantity"), "line_item.quantity") or Decimal("1"),
        unit_price=_to_decimal(item.get("unit_price"), "line_item.unit_price") or Decimal("0"),
        line_total=_to_decimal(item.get("line_total"), "line_item.line_total") or Decimal("0"),
    )


def _to_decimal(value: Any, field_name: str) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"could not parse {field_name} as a decimal: {value!r}") from exc


def _to_date(value: Any, field_name: str) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise ValueError(f"could not parse {field_name} as an ISO date: {value!r}") from exc
