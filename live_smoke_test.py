import json
import sys
import tempfile
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from pipeline.graph import build_graph, resume_run, start_run
from pipeline.schemas import IncomingDocument, PurchaseOrder, VendorRecord
from pipeline.storage import Repository
from web.server import _build_llm_clients

FIXTURES_DIR = Path(__file__).resolve().parent / "data" / "invoices"
DATA_DIR = Path(__file__).resolve().parent / "data"

CASES = [
    {"name": "happy_path", "fixture": "happy_path.pdf", "expected_outcome": "AUTO_APPROVE", "expected_reason_codes": set()},
    {"name": "edge_a_split_1", "fixture": "edge_a_split_1.pdf", "expected_outcome": "AUTO_APPROVE", "expected_reason_codes": set()},
    {"name": "edge_a_split_2", "fixture": "edge_a_split_2.pdf", "expected_outcome": "AUTO_APPROVE", "expected_reason_codes": set()},
    {"name": "edge_b_tax", "fixture": "edge_b_tax.pdf", "expected_outcome": "AUTO_APPROVE", "expected_reason_codes": set()},
    {"name": "edge_c_dup_1", "fixture": "edge_c_dup_1.pdf", "expected_outcome": "AUTO_APPROVE", "expected_reason_codes": set()},
    {"name": "edge_c_dup_2", "fixture": "edge_c_dup_2.pdf", "expected_outcome": "NEEDS_REVIEW", "expected_reason_codes": {"DUPLICATE_SUSPECTED", "PO_BALANCE_EXCEEDED"}},
    {"name": "edge_d_degraded", "fixture": "edge_d_degraded.png", "expected_outcome": "NEEDS_REVIEW", "expected_reason_codes": None},
]


def main() -> int:
    extraction_client, triage_client = _build_llm_clients()

    vendors = [VendorRecord.model_validate(v) for v in json.loads((DATA_DIR / "vendors.json").read_text())]
    pos = [PurchaseOrder.model_validate(p) for p in json.loads((DATA_DIR / "purchase_orders.json").read_text())]

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        repo = Repository(str(tmp_path / "live.db"))
        repo.init_db()
        repo.seed_reference_data(vendors, pos)

        results = []
        for i, case in enumerate(CASES):
            app = build_graph(repo, extraction_client, triage_client, str(tmp_path / f"cp_{i}.db"))
            document = IncomingDocument(
                doc_id=f"doc-{case['name']}", source="folder", received_at="2026-08-02T00:00:00Z",
                sender=None, filename=case["fixture"], content_path=str(FIXTURES_DIR / case["fixture"]),
            )
            run_id = f"run-{case['name']}"
            try:
                start_run(app, run_id, document, repo)
                result = resume_run(app, run_id, approved=True)
                decision = result["decision"]
                actual_outcome = decision.outcome
                actual_reason_codes = sorted(decision.reason_codes)
                if case["expected_reason_codes"] is None:
                    passed = actual_outcome == case["expected_outcome"]
                else:
                    passed = actual_outcome == case["expected_outcome"] and set(actual_reason_codes) == case["expected_reason_codes"]
                results.append({
                    "case": case["name"], "pass": passed,
                    "expected_outcome": case["expected_outcome"], "actual_outcome": actual_outcome,
                    "actual_reason_codes": actual_reason_codes, "explanation": decision.explanation,
                })
            except Exception as exc:
                results.append({"case": case["name"], "pass": False, "error": f"{type(exc).__name__}: {exc}"})
            finally:
                app._checkpointer_ctx.__exit__(None, None, None)

    for r in results:
        status = "PASS" if r["pass"] else "FAIL"
        print(f"[{status}] {r['case']}")
        if "error" in r:
            print(f"    ERROR: {r['error']}")
        else:
            print(f"    expected={r['expected_outcome']}  actual={r['actual_outcome']}  reasons={r['actual_reason_codes']}")
            print(f"    explanation: {r['explanation']}")

    all_pass = all(r["pass"] for r in results)
    print()
    print(f"TOTAL: {sum(r['pass'] for r in results)}/{len(results)} passed")
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
