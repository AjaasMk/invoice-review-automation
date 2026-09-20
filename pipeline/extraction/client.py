from typing import Protocol


class ExtractionClient(Protocol):
    def structure_from_text(self, text: str) -> dict: ...
    def structure_from_image(self, image_bytes: bytes, media_type: str) -> dict: ...


EXTRACTION_INSTRUCTIONS = (
    "Extract invoice fields as JSON with exactly these keys: invoice_number, "
    "invoice_date (YYYY-MM-DD), vendor_name, po_reference, line_items (list of "
    "objects with description, quantity, unit_price, line_total), subtotal, "
    "tax_amount, total_amount, currency. Use null for any field not present in "
    "the document. Return only the JSON object, no surrounding prose."
)
