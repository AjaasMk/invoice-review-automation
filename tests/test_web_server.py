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


def test_workspace_exposes_reference_data_without_credentials(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret-must-not-appear")
    monkeypatch.setenv("GMAIL_IMAP_APP_PASSWORD", "mail-secret-must-not-appear")
    with _seeded_app(tmp_path)[0] as client:
        response = client.get("/api/workspace")
        assert response.status_code == 200
        data = response.json()
        assert data["vendors"][0]["vendor_id"] == "V-ACME"
        assert data["purchase_orders"][0]["remaining_balance"] == "5000"
        assert data["runtime"]["email_enabled"] is False
        assert set(data["runtime"]) == {"extraction_client", "model", "email_enabled", "poll_interval_seconds"}
        assert "secret-must-not-appear" not in response.text


def test_workspace_reflects_processed_po_balance(tmp_path: Path) -> None:
    with _seeded_app(tmp_path)[0] as client:
        result = client.post("/api/upload", files={"file": ("invoice.pdf", FIXTURE.read_bytes(), "application/pdf")})
        run_id = result.json()["run_id"]
        client.post(f"/api/gate/{run_id}/approve")
        for _ in range(100):
            run = client.get(f"/api/runs/{run_id}").json()
            if run["status"] == "DONE":
                break
            time.sleep(0.05)
        assert run["status"] == "DONE"
        assert run["po_reference"] == "PO-1001"
        order = client.get("/api/workspace").json()["purchase_orders"][0]
        assert order["invoiced_to_date"] == "5000"
        assert order["remaining_balance"] == "0"


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


def test_upload_rejects_unsupported_files(tmp_path: Path) -> None:
    with _seeded_app(tmp_path)[0] as client:
        response = client.post("/api/upload", files={"file": ("notes.txt", b"not an invoice", "text/plain")})
        assert response.status_code == 415


def test_gate_decision_cannot_be_submitted_twice(tmp_path: Path) -> None:
    with _seeded_app(tmp_path)[0] as client:
        with open(FIXTURE, "rb") as f:
            response = client.post("/api/upload", files={"file": ("happy_path.pdf", f, "application/pdf")})
        run_id = response.json()["run_id"]
        assert client.post(f"/api/gate/{run_id}/reject").status_code == 200
        for _ in range(40):
            if client.get(f"/api/runs/{run_id}").json()["status"] == "REJECTED_AT_GATE":
                break
            time.sleep(0.05)
        assert client.post(f"/api/gate/{run_id}/approve").status_code == 409


def test_demo_reset_requires_confirmation_and_clears_runs(tmp_path: Path) -> None:
    with _seeded_app(tmp_path)[0] as client:
        with open(FIXTURE, "rb") as f:
            response = client.post("/api/upload", files={"file": ("happy_path.pdf", f, "application/pdf")})
        assert response.status_code == 200
        assert client.post("/api/demo/reset").status_code == 400
        reset_response = client.post("/api/demo/reset?confirm=true")
        assert reset_response.status_code == 200
        assert reset_response.json()["runs_cleared"] == 1
        assert client.get("/api/runs").json() == []
