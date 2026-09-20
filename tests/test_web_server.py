import time
from pathlib import Path

from fastapi.testclient import TestClient

from pipeline.intake.folder_source import FolderSource
from pipeline.intake.upload_source import UploadSource
from pipeline.storage import Repository
from web.server import create_app

FIXTURE = Path(__file__).resolve().parent.parent / "data" / "invoices" / "happy_path.pdf"


class FakeExtractionClient:
    def structure_from_text(self, text: str) -> dict:
        return {"invoice_number": "INV-3001", "invoice_date": "2026-08-02", "vendor_name": "Acme Corp", "po_reference": "PO-1001", "line_items": [], "subtotal": 5000, "tax_amount": 0, "total_amount": 5000, "currency": "USD"}

    def structure_from_image(self, image_bytes: bytes, media_type: str) -> dict:
        raise AssertionError("not expected for this fixture")


class FakeTriageClient:
    def triage_text(self, text: str) -> dict:
        return {"looks_like_invoice": True, "preview_text": "Invoice from Acme Corp", "reason": "has invoice number and total"}

    def triage_image(self, image_bytes: bytes, media_type: str) -> dict:
        raise AssertionError("not expected for this fixture")


def _seeded_app(tmp_path: Path) -> tuple[TestClient, Repository]:
    from datetime import date
    from decimal import Decimal

    from pipeline.schemas import PurchaseOrder, VendorRecord

    repo = Repository(str(tmp_path / "app.db"))
    repo.init_db()
    repo.seed_reference_data(
        [VendorRecord(vendor_id="V-ACME", canonical_name="Acme Corporation Pvt Ltd", aliases=["Acme Corp"], approved=True)],
        [PurchaseOrder(po_id="PO-1001", vendor_id="V-ACME", amount=Decimal("5000"), issued_date=date(2026, 8, 1), tax_treatment="exclusive")],
    )
    folder_source = FolderSource(str(tmp_path / "inbox"), str(tmp_path / "intake_storage"))
    upload_source = UploadSource(str(tmp_path / "uploads"))
    app = create_app(
        repo,
        FakeExtractionClient(),
        FakeTriageClient(),
        folder_source,
        upload_source,
        str(tmp_path / "checkpoints.db"),
        poll_interval_seconds=999.0,
    )
    return TestClient(app), repo


def test_upload_triages_and_appears_in_gate_pending(tmp_path: Path) -> None:
    with _seeded_app(tmp_path)[0] as client:
        with open(FIXTURE, "rb") as f:
            response = client.post("/api/upload", files={"file": ("happy_path.pdf", f, "application/pdf")})
        assert response.status_code == 200
        run_id = response.json()["run_id"]

        pending = client.get("/api/gate/pending").json()
        assert any(p["run_id"] == run_id for p in pending)
        matching = next(p for p in pending if p["run_id"] == run_id)
        assert matching["triage"]["looks_like_invoice"] is True


def test_approve_runs_pipeline_to_auto_approve(tmp_path: Path) -> None:
    with _seeded_app(tmp_path)[0] as client:
        with open(FIXTURE, "rb") as f:
            response = client.post("/api/upload", files={"file": ("happy_path.pdf", f, "application/pdf")})
        run_id = response.json()["run_id"]

        approve_response = client.post(f"/api/gate/{run_id}/approve")
        assert approve_response.status_code == 200

        final_status = None
        for _ in range(40):
            run = client.get(f"/api/runs/{run_id}").json()
            final_status = run["status"]
            if final_status == "DONE":
                assert run["decision"]["outcome"] == "AUTO_APPROVE"
                break
            time.sleep(0.05)
        assert final_status == "DONE"


def test_reject_marks_run_rejected(tmp_path: Path) -> None:
    with _seeded_app(tmp_path)[0] as client:
        with open(FIXTURE, "rb") as f:
            response = client.post("/api/upload", files={"file": ("happy_path.pdf", f, "application/pdf")})
        run_id = response.json()["run_id"]

        client.post(f"/api/gate/{run_id}/reject")

        final_status = None
        for _ in range(40):
            run = client.get(f"/api/runs/{run_id}").json()
            final_status = run["status"]
            if final_status == "REJECTED_AT_GATE":
                break
            time.sleep(0.05)
        assert final_status == "REJECTED_AT_GATE"
