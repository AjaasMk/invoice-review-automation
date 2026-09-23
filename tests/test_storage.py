from decimal import Decimal
from datetime import date

from pipeline.schemas import Decision, Invoice, PurchaseOrder, VendorRecord
from pipeline.storage import Repository


def _repo(tmp_path) -> Repository:
    repo = Repository(str(tmp_path / "test.db"))
    repo.init_db()
    return repo


def test_seed_and_read_back_vendors_and_pos(tmp_path) -> None:
    repo = _repo(tmp_path)
    vendor = VendorRecord(vendor_id="V-ACME", canonical_name="Acme Corporation Pvt Ltd", aliases=["Acme Corp"], approved=True)
    po = PurchaseOrder(po_id="PO-1001", vendor_id="V-ACME", amount=Decimal("5000"), issued_date=date(2026, 8, 1), tax_treatment="exclusive")
    repo.seed_reference_data([vendor], [po])
    vendors = repo.get_vendors()
    pos = repo.get_purchase_orders()
    assert vendors[0].vendor_id == "V-ACME"
    assert vendors[0].aliases == ["Acme Corp"]
    assert pos[0].amount == Decimal("5000")


def test_update_po_balance_persists(tmp_path) -> None:
    repo = _repo(tmp_path)
    po = PurchaseOrder(po_id="PO-1002", vendor_id="V-GLOBEX", amount=Decimal("10000"), issued_date=date(2026, 8, 5), tax_treatment="exclusive")
    repo.seed_reference_data([], [po])
    repo.update_po_balance("PO-1002", Decimal("6000"))
    pos = repo.get_purchase_orders()
    assert pos[0].invoiced_to_date == Decimal("6000")


def test_reseeding_reference_data_preserves_po_balance(tmp_path) -> None:
    repo = _repo(tmp_path)
    po = PurchaseOrder(po_id="PO-1002", vendor_id="V-GLOBEX", amount=Decimal("10000"), issued_date=date(2026, 8, 5), tax_treatment="exclusive")
    repo.seed_reference_data([], [po])
    repo.update_po_balance("PO-1002", Decimal("6000"))
    repo.seed_reference_data([], [po])
    assert repo.get_purchase_orders()[0].invoiced_to_date == Decimal("6000")


def test_po_balance_compare_and_set_rejects_a_stale_update(tmp_path) -> None:
    repo = _repo(tmp_path)
    po = PurchaseOrder(po_id="PO-1002", vendor_id="V-GLOBEX", amount=Decimal("10000"), issued_date=date(2026, 8, 5), tax_treatment="exclusive")
    repo.seed_reference_data([], [po])
    assert repo.update_po_balance_if_current("PO-1002", Decimal("0"), Decimal("6000")) is True
    assert repo.update_po_balance_if_current("PO-1002", Decimal("0"), Decimal("4000")) is False
    assert repo.get_purchase_orders()[0].invoiced_to_date == Decimal("6000")


def test_run_lifecycle_and_decision(tmp_path) -> None:
    repo = _repo(tmp_path)
    repo.create_run("run-1", "doc-1", "RUNNING")
    repo.update_run_status("run-1", "DONE")
    decision = Decision(outcome="AUTO_APPROVE", reason_codes=[], explanation="ok", evidence=[])
    repo.set_run_decision("run-1", decision)
    run = repo.get_run("run-1")
    assert run is not None
    assert run["status"] == "DONE"
    assert run["decision"]["outcome"] == "AUTO_APPROVE"


def test_stages_persist_in_order(tmp_path) -> None:
    repo = _repo(tmp_path)
    repo.create_run("run-2", "doc-2", "RUNNING")
    repo.append_stage("run-2", "triage", {"looks_like_invoice": True})
    repo.append_stage("run-2", "extract", {"invoice_number": "INV-1"})
    stages = repo.get_stages("run-2")
    assert [s["stage"] for s in stages] == ["triage", "extract"]
    assert stages[1]["payload"]["invoice_number"] == "INV-1"


def test_list_runs_returns_all_runs(tmp_path) -> None:
    repo = _repo(tmp_path)
    repo.create_run("run-a", "doc-a", "RUNNING")
    repo.create_run("run-b", "doc-b", "RUNNING")
    runs = repo.list_runs()
    assert {r["run_id"] for r in runs} == {"run-a", "run-b"}


def test_run_summary_includes_intake_and_invoice_fields(tmp_path) -> None:
    repo = _repo(tmp_path)
    repo.create_run("run-summary", "doc-summary", "DONE")
    repo.append_stage("run-summary", "intake", {"filename": "invoice.pdf", "source": "upload"})
    repo.append_stage("run-summary", "validate", {
        "invoice_number": "INV-42", "vendor_name_raw": "Acme Corp", "total_amount": "125.00", "currency": "USD"
    })
    run = repo.get_run("run-summary")
    assert run["filename"] == "invoice.pdf"
    assert run["invoice"] == {
        "invoice_number": "INV-42", "vendor_name": "Acme Corp", "total_amount": "125.00", "currency": "USD"
    }


def test_prior_invoices_filtered_by_vendor_and_excludes_current_run(tmp_path) -> None:
    repo = _repo(tmp_path)
    invoice = Invoice(
        invoice_number="INV-2001",
        invoice_date=date(2026, 8, 1),
        vendor_name_raw="Acme Corp",
        total_amount=Decimal("2000"),
        extraction_path="pdf_text",
        extraction_confidence=0.9,
    )
    repo.save_invoice_record("run-x", "V-ACME", invoice)
    repo.save_invoice_record("run-y", "V-GLOBEX", invoice)
    prior = repo.get_prior_invoices("V-ACME", exclude_run_id="run-z")
    assert len(prior) == 1
    assert prior[0].invoice_number == "INV-2001"
    prior_excluded = repo.get_prior_invoices("V-ACME", exclude_run_id="run-x")
    assert prior_excluded == []


def test_find_prior_run_by_content_hash_returns_completed_decision(tmp_path) -> None:
    repo = _repo(tmp_path)
    repo.create_run("run-old", "doc-old", "DONE", content_sha256="abc123")
    repo.set_run_decision("run-old", Decision(outcome="AUTO_APPROVE", reason_codes=[], explanation="ok", evidence=[]))
    repo.create_run("run-new", "doc-new", "RUNNING", content_sha256="abc123")
    prior = repo.find_prior_run_by_content_hash("abc123", exclude_run_id="run-new")
    assert prior is not None
    assert prior["run_id"] == "run-old"
    assert prior["decision"]["outcome"] == "AUTO_APPROVE"


def test_reset_demo_state_clears_history_and_restores_po_balance(tmp_path) -> None:
    repo = _repo(tmp_path)
    po = PurchaseOrder(po_id="PO-9", vendor_id="V-ACME", amount=Decimal("500"), issued_date=date(2026, 8, 1), tax_treatment="exclusive")
    repo.seed_reference_data([], [po])
    repo.update_po_balance("PO-9", Decimal("400"))
    repo.create_run("run-reset", "doc-reset", "DONE", content_sha256="hash")
    repo.append_stage("run-reset", "intake", {"filename": "invoice.pdf"})
    invoice = Invoice(vendor_name_raw="Acme", extraction_path="pdf_text", extraction_confidence=1.0)
    repo.save_invoice_record("run-reset", "V-ACME", invoice)

    summary = repo.reset_demo_state()

    assert summary == {"runs_cleared": 1, "invoice_records_cleared": 1}
    assert repo.list_runs() == []
    assert repo.get_prior_invoices("V-ACME", exclude_run_id="other") == []
    assert repo.get_purchase_orders()[0].invoiced_to_date == Decimal("0")
