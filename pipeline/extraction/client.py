from typing import Protocol


class ExtractionClient(Protocol):
    def structure_from_text(self, text: str) -> dict: ...
    def structure_from_image(self, image_bytes: bytes, media_type: str) -> dict: ...


EXTRACTION_INSTRUCTIONS = (
    "Extract invoice fields as JSON with exactly these keys: invoice_number, "
    "invoice_date (YYYY-MM-DD), vendor_name, po_reference, line_items (list of "
    "objects with description, quantity, unit_price, line_total), subtotal, "
    "tax_amount, total_amount, currency. Use null for any field that is missing "
    "OR that you cannot read with high confidence because the document is blurry, "
    "skewed, cropped, or otherwise degraded. This is a financial document used to "
    "approve real payments: a fabricated value is worse than an honest null. Never "
    "invent a plausible-looking placeholder (e.g. a generic vendor name, a round "
    "invoice number, an example line item) to fill a field you cannot actually "
    "read. If most of the document is illegible, return null for every field you "
    "are not certain of rather than guessing a complete-looking invoice. Return "
    "only the JSON object, no surrounding prose."
)

EMPTY_EXTRACTION_RESULT = {
    "invoice_number": None,
    "invoice_date": None,
    "vendor_name": None,
    "po_reference": None,
    "line_items": [],
    "subtotal": None,
    "tax_amount": None,
    "total_amount": None,
    "currency": None,
}
