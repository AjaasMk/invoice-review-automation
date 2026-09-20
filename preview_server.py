import sys
import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pipeline.intake.folder_source import FolderSource
from pipeline.intake.upload_source import UploadSource
from pipeline.schemas import PurchaseOrder, VendorRecord
from pipeline.storage import Repository
from web.server import create_app


class FakeExtractionClient:
    def structure_from_text(self, text: str) -> dict:
        return {
            "invoice_number": "INV-3001", "invoice_date": "2026-08-02", "vendor_name": "Acme Corp",
            "po_reference": "PO-1001", "line_items": [], "subtotal": 5000, "tax_amount": 0,
            "total_amount": 5000, "currency": "USD",
        }

    def structure_from_image(self, image_bytes: bytes, media_type: str) -> dict:
        return {
            "invoice_number": None, "invoice_date": None, "vendor_name": "Acme Corp",
            "po_reference": None, "line_items": [], "subtotal": None, "tax_amount": None,
            "total_amount": None, "currency": "USD",
        }


class FakeTriageClient:
    def triage_text(self, text: str) -> dict:
        return {"looks_like_invoice": True, "preview_text": "Invoice from Acme Corp for consulting services rendered in August, referencing PO-1001.", "reason": "has invoice number, vendor, and total"}

    def triage_image(self, image_bytes: bytes, media_type: str) -> dict:
        return {"looks_like_invoice": True, "preview_text": "A scanned document, possibly an invoice, image quality is degraded.", "reason": "low confidence"}


tmp_dir = Path(tempfile.mkdtemp())
repo = Repository(str(tmp_dir / "app.db"))
repo.init_db()
repo.seed_reference_data(
    [
        VendorRecord(vendor_id="V-ACME", canonical_name="Acme Corporation Pvt Ltd", aliases=["Acme Corp"], approved=True),
        VendorRecord(vendor_id="V-GLOBEX", canonical_name="Globex Industries LLC", aliases=["Globex"], approved=True),
    ],
    [
        PurchaseOrder(po_id="PO-1001", vendor_id="V-ACME", amount=Decimal("5000"), issued_date=date(2026, 8, 1), tax_treatment="exclusive"),
    ],
)
folder_source = FolderSource(str(tmp_dir / "inbox"), str(tmp_dir / "intake_storage"))
upload_source = UploadSource(str(tmp_dir / "uploads"))

app = create_app(
    repo, FakeExtractionClient(), FakeTriageClient(), folder_source, upload_source,
    str(tmp_dir / "checkpoints.db"), poll_interval_seconds=999.0,
)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8123, log_level="warning")
