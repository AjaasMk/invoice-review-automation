import json
import sys
import tempfile
from pathlib import Path

from pipeline.graph import build_graph, resume_run, start_run
from pipeline.schemas import IncomingDocument, PurchaseOrder, VendorRecord
from pipeline.storage import Repository

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "data" / "invoices"
DATA_DIR = Path(__file__).resolve().parent.parent / "data"


class StaticExtractionClient:
    def __init__(self, raw: dict) -> None:
        self._raw = raw

    def structure_from_text(self, text: str) -> dict:
        return self._raw

    def structure_from_image(self, image_bytes: bytes, media_type: str) -> dict:
        return self._raw


class StaticTriageClient:
    def triage_text(self, text: str) -> dict:
        return {"looks_like_invoice": True, "preview_text": "golden eval fixture", "reason": "golden eval"}

    def triage_image(self, image_bytes: bytes, media_type: str) -> dict:
        return {"looks_like_invoice": True, "preview_text": "golden eval fixture", "reason": "golden eval"}


CASES = [
    {
        "name": "happy_path",
        "fixture": "happy_path.pdf",
        "raw": {
            "invoice_number": "INV-3001", "invoice_date": "2026-08-02", "vendor_name": "Acme Corp",
            "po_reference": "PO-1001", "line_items": [], "subtotal": 5000, "tax_amount": 0,
            "total_amount": 5000, "currency": "USD",
        },
        "expected_outcome": "AUTO_APPROVE",
        "expected_reason_codes": set(),
    },
    {
        "name": "edge_a_split_1",
        "fixture": "edge_a_split_1.pdf",
        "raw": {
            "invoice_number": "INV-4001", "invoice_date": "2026-08-06", "vendor_name": "Globex Industries LLC",
            "po_reference": "PO-1002", "line_items": [], "subtotal": 6000, "tax_amount": 0,
            "total_amount": 6000, "currency": "USD",
        },
        "expected_outcome": "AUTO_APPROVE",
        "expected_reason_codes": set(),
    },
    {
        "name": "edge_a_split_2",
        "fixture": "edge_a_split_2.pdf",
        "raw": {
            "invoice_number": "INV-4002", "invoice_date": "2026-08-20", "vendor_name": "Globex Industries LLC",
            "po_reference": "PO-1002", "line_items": [], "subtotal": 4000, "tax_amount": 0,
            "total_amount": 4000, "currency": "USD",
        },
        "expected_outcome": "AUTO_APPROVE",
        "expected_reason_codes": set(),
    },
    {
        "name": "edge_b_tax",
        "fixture": "edge_b_tax.pdf",
        "raw": {
            "invoice_number": "INV-5001", "invoice_date": "2026-08-11", "vendor_name": "Acme Corp",
            "po_reference": "PO-1003", "line_items": [], "subtotal": 3000, "tax_amount": 240,
            "total_amount": 3240, "currency": "USD",
        },
        "expected_outcome": "AUTO_APPROVE",
        "expected_reason_codes": set(),
    },
    {
        "name": "edge_c_dup_1",
        "fixture": "edge_c_dup_1.pdf",
        "raw": {
            "invoice_number": "INV-6001", "invoice_date": "2026-08-13", "vendor_name": "Acme Corp",
            "po_reference": "PO-1004", "line_items": [], "subtotal": 2000, "tax_amount": 0,
            "total_amount": 2000, "currency": "USD",
        },
        "expected_outcome": "AUTO_APPROVE",
        "expected_reason_codes": set(),
    },
    {
        "name": "edge_c_dup_2",
        "fixture": "edge_c_dup_2.pdf",
        "raw": {
            "invoice_number": "INV-6002", "invoice_date": "2026-08-16", "vendor_name": "Acme Corp",
            "po_reference": "PO-1004", "line_items": [], "subtotal": 1995, "tax_amount": 0,
            "total_amount": 1995, "currency": "USD",
        },
        "expected_outcome": "NEEDS_REVIEW",
        "expected_reason_codes": {"DUPLICATE_SUSPECTED", "PO_BALANCE_EXCEEDED"},
    },
    {
        "name": "edge_d_degraded",
        "fixture": "edge_d_degraded.png",
        "raw": {
            "invoice_number": None, "invoice_date": None, "vendor_name": "Acme Corp",
            "po_reference": None, "line_items": [], "subtotal": None, "tax_amount": None,
            "total_amount": None, "currency": "USD",
        },
        "expected_outcome": "NEEDS_REVIEW",
        "expected_reason_codes": {"LOW_EXTRACTION_CONFIDENCE", "MISSING_REQUIRED_FIELD"},
    },
]


def load_reference_data(repo: Repository) -> None:
    vendors_raw = json.loads((DATA_DIR / "vendors.json").read_text())
    pos_raw = json.loads((DATA_DIR / "purchase_orders.json").read_text())
    vendors = [VendorRecord.model_validate(v) for v in vendors_raw]
    purchase_orders = [PurchaseOrder.model_validate(p) for p in pos_raw]
    repo.seed_reference_data(vendors, purchase_orders)


def run_case(repo: Repository, case: dict, checkpoint_dir: Path, index: int) -> dict:
    client = StaticExtractionClient(case["raw"])
    triage_client = StaticTriageClient()
    checkpoint_path = str(checkpoint_dir / f"{index}.db")
    app = build_graph(repo, client, triage_client, checkpoint_path)

    document = IncomingDocument(
        doc_id=f"doc-{case['name']}",
        source="folder",
        received_at="2026-08-02T00:00:00Z",
        sender=None,
        filename=case["fixture"],
        content_path=str(FIXTURES_DIR / case["fixture"]),
    )
    run_id = f"run-{case['name']}"
    try:
        start_run(app, run_id, document, repo)
        result = resume_run(app, run_id, approved=True)
        decision = result["decision"]

        actual_outcome = decision.outcome
        actual_reason_codes = set(decision.reason_codes)
        passed = actual_outcome == case["expected_outcome"] and actual_reason_codes == case["expected_reason_codes"]

        return {
            "case": case["name"],
            "pass": passed,
            "expected": {"outcome": case["expected_outcome"], "reason_codes": sorted(case["expected_reason_codes"])},
            "actual": {"outcome": actual_outcome, "reason_codes": sorted(actual_reason_codes)},
        }
    finally:
        app._checkpointer_ctx.__exit__(None, None, None)


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        repo = Repository(str(tmp_path / "eval.db"))
        repo.init_db()
        load_reference_data(repo)

        results = [run_case(repo, case, tmp_path, i) for i, case in enumerate(CASES)]

    overall_pass = all(r["pass"] for r in results)
    output = {
        "results": results,
        "summary": {"total": len(results), "passed": sum(r["pass"] for r in results), "all_pass": overall_pass},
    }
    print(json.dumps(output, indent=2))
    return 0 if overall_pass else 1


if __name__ == "__main__":
    sys.exit(main())
