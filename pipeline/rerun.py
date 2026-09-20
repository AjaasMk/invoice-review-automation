import argparse
import json

from pipeline.decision import decide
from pipeline.normalize import normalize_invoice
from pipeline.po_match import match_po
from pipeline.schemas import Invoice, MatchResult, VendorRecord
from pipeline.storage import Repository
from pipeline.validate import validate_invoice
from pipeline.vendor_resolve import resolve_vendor

RERUNNABLE_STAGES = ["normalize", "validate", "vendor_resolve", "po_match", "decide"]


def rerun_stage(repo: Repository, run_id: str, stage: str) -> dict:
    if stage not in RERUNNABLE_STAGES:
        raise ValueError(f"stage '{stage}' is not rerunnable in isolation; choose one of {RERUNNABLE_STAGES}")

    stages_by_name = {s["stage"]: s["payload"] for s in repo.get_stages(run_id)}

    if stage == "normalize":
        if "extract" not in stages_by_name:
            raise ValueError(f"run {run_id} has no persisted 'extract' stage to rerun normalize from")
        extract_payload = stages_by_name["extract"]
        invoice = normalize_invoice(extract_payload["raw"], extract_payload["extraction_path"], extract_payload["confidence"])
        return invoice.model_dump(mode="json")

    if stage == "validate":
        invoice = Invoice.model_validate(stages_by_name["normalize"])
        return validate_invoice(invoice).model_dump(mode="json")

    if stage == "vendor_resolve":
        invoice = Invoice.model_validate(stages_by_name["validate"])
        vendor = resolve_vendor(invoice.vendor_name_raw, repo.get_vendors())
        return {"vendor": vendor.model_dump() if vendor else None}

    if stage == "po_match":
        invoice = Invoice.model_validate(stages_by_name["validate"])
        vendor_payload = stages_by_name["vendor_resolve"]["vendor"]
        vendor = VendorRecord.model_validate(vendor_payload) if vendor_payload else None
        prior_invoices = repo.get_prior_invoices(vendor.vendor_id, run_id) if vendor is not None else []
        match = match_po(invoice, vendor, repo.get_purchase_orders(), prior_invoices)
        return match.model_dump(mode="json")

    invoice = Invoice.model_validate(stages_by_name["validate"])
    match = MatchResult.model_validate(stages_by_name["po_match"])
    return decide(invoice, match).model_dump(mode="json")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="runs/app.db")
    parser.add_argument("--run", required=True)
    parser.add_argument("--from", dest="stage", required=True, choices=RERUNNABLE_STAGES)
    args = parser.parse_args()

    repo = Repository(args.db)
    result = rerun_stage(repo, args.run, args.stage)
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
