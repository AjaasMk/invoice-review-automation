from pathlib import Path

from pipeline.graph import build_graph, resume_run, start_run
from pipeline.schemas import IncomingDocument
from pipeline.storage import Repository

FIXTURE = Path(__file__).resolve().parent.parent / "data" / "invoices" / "happy_path.pdf"


class FakeExtractionClient:
    def structure_from_text(self, text: str) -> dict:
        return {
            "invoice_number": "INV-3001",
            "invoice_date": "2026-08-02",
            "vendor_name": "Acme Corp",
            "po_reference": "PO-1001",
            "line_items": [{"description": "Consulting", "quantity": 1, "unit_price": 5000, "line_total": 5000}],
            "subtotal": 5000,
            "tax_amount": 0,
            "total_amount": 5000,
            "currency": "USD",
        }

    def structure_from_image(self, image_bytes: bytes, media_type: str) -> dict:
        raise AssertionError("happy path fixture has a text layer; vision path should not be called")


class FakeTriageClient:
    def triage_text(self, text: str) -> dict:
        return {"looks_like_invoice": True, "preview_text": "Invoice from Acme Corp", "reason": "has invoice number and total"}

    def triage_image(self, image_bytes: bytes, media_type: str) -> dict:
        raise AssertionError("happy path fixture has a text layer; vision path should not be called")


def _seeded_repo(tmp_path: Path) -> Repository:
    from datetime import date
    from decimal import Decimal

    from pipeline.schemas import PurchaseOrder, VendorRecord

    repo = Repository(str(tmp_path / "test.db"))
    repo.init_db()
    repo.seed_reference_data(
        [VendorRecord(vendor_id="V-ACME", canonical_name="Acme Corporation Pvt Ltd", aliases=["Acme Corp"], approved=True)],
        [PurchaseOrder(po_id="PO-1001", vendor_id="V-ACME", amount=Decimal("5000"), issued_date=date(2026, 8, 1), tax_treatment="exclusive")],
    )
    return repo


def test_happy_path_pauses_at_gate_then_auto_approves_on_resume(tmp_path: Path) -> None:
    repo = _seeded_repo(tmp_path)
    app = build_graph(repo, FakeExtractionClient(), FakeTriageClient(), str(tmp_path / "checkpoints.db"))
    document = IncomingDocument(doc_id="doc-1", source="folder", received_at="2026-08-02T00:00:00Z", sender=None, filename="happy_path.pdf", content_path=str(FIXTURE))

    start_run(app, "run-1", document, repo)
    run_after_start = repo.get_run("run-1")
    assert run_after_start["status"] == "AWAITING_APPROVAL"

    result = resume_run(app, "run-1", approved=True)
    assert result["decision"].outcome == "AUTO_APPROVE"
    run_after_resume = repo.get_run("run-1")
    assert run_after_resume["status"] == "DONE"
    assert run_after_resume["decision"]["outcome"] == "AUTO_APPROVE"

    updated_pos = {po.po_id: po for po in repo.get_purchase_orders()}
    assert updated_pos["PO-1001"].invoiced_to_date == updated_pos["PO-1001"].amount


def test_rejected_at_gate_never_reaches_extraction(tmp_path: Path) -> None:
    repo = _seeded_repo(tmp_path)
    app = build_graph(repo, FakeExtractionClient(), FakeTriageClient(), str(tmp_path / "checkpoints.db"))
    document = IncomingDocument(doc_id="doc-2", source="folder", received_at="2026-08-02T00:00:00Z", sender=None, filename="happy_path.pdf", content_path=str(FIXTURE))

    start_run(app, "run-2", document, repo)
    resume_run(app, "run-2", approved=False)
    run_after = repo.get_run("run-2")
    assert run_after["status"] == "REJECTED_AT_GATE"
    assert run_after["decision"] is None
