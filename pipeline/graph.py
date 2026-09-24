import sqlite3
from contextlib import closing
from typing import Any, Callable, TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, StateGraph
from langgraph.types import Command, interrupt

from pipeline.decision import decide
from pipeline.extraction.client import ExtractionClient
from pipeline.extraction.router import route_and_extract
from pipeline.normalize import normalize_invoice
from pipeline.po_match import match_po
from pipeline.schemas import Decision, IncomingDocument, Invoice, MatchResult, TriageResult, VendorRecord
from pipeline.storage import Repository
from pipeline.triage import TriageClient
from pipeline.validate import validate_invoice
from pipeline.vendor_resolve import resolve_vendor


class GraphState(TypedDict, total=False):
    document: IncomingDocument
    run_id: str
    triage: TriageResult
    approved: bool
    raw_extraction: dict
    extraction_path: str
    extraction_confidence: float
    invoice: Invoice
    vendor: VendorRecord | None
    match: MatchResult
    decision: Decision
    exact_duplicate: bool


def build_graph(
    repo: Repository,
    extraction_client: ExtractionClient,
    triage_client: TriageClient,
    checkpoint_db_path: str,
    on_stage: Callable[[str, str, dict], None] | None = None,
) -> Any:
    def started(run_id: str, stage: str) -> None:
        if on_stage is not None:
            on_stage(run_id, stage, {"event": "started"})

    def record(run_id: str, stage: str, payload: dict) -> None:
        repo.append_stage(run_id, stage, payload)
        if on_stage is not None:
            on_stage(run_id, stage, {"event": "completed", "output": payload})

    def node_triage(state: GraphState) -> dict:
        started(state["run_id"], "triage")
        # Intake is deliberately human-first. Gmail items have already been put in
        # the review label by an employee, and uploads/folder items pause at the
        # same gate. Calling the vision model here and again after approval made
        # a scanned invoice pay for two serial AI requests with no added control.
        triage_result = TriageResult(
            looks_like_invoice=True,
            preview_text="Attachment received and ready for human intake review.",
            reason="AI extraction begins only after a reviewer approves this item.",
        )
        record(state["run_id"], "triage", triage_result.model_dump())
        return {"triage": triage_result}

    def node_deduplicate(state: GraphState) -> dict:
        """Stop exact re-uploads before model calls; invoice-level matching still follows later."""
        started(state["run_id"], "deduplicate")
        prior = repo.find_prior_run_by_content_hash(state["document"].content_sha256, state["run_id"])
        payload = {
            "content_sha256": state["document"].content_sha256,
            "exact_file_duplicate": prior is not None,
            "original_run_id": prior["run_id"] if prior else None,
            "original_decision": prior["decision"]["outcome"] if prior else None,
        }
        record(state["run_id"], "deduplicate", payload)
        if prior is None:
            return {"exact_duplicate": False}

        decision = Decision(
            outcome="NEEDS_REVIEW",
            reason_codes=["EXACT_FILE_DUPLICATE"],
            explanation=(
                f"An identical file was already processed in run {prior['run_id']} "
                f"with outcome {prior['decision']['outcome']}; no second extraction was performed."
            ),
            evidence=[
                {
                    "label": "exact_file_duplicate",
                    "detail": f"SHA-256 matches prior run {prior['run_id']}",
                    "weight": 1.0,
                }
            ],
        )
        record(state["run_id"], "decide", decision.model_dump(mode="json"))
        repo.set_run_decision(state["run_id"], decision)
        repo.update_run_status(state["run_id"], "DONE")
        if on_stage is not None:
            on_stage(state["run_id"], "__done__", {"status": "DONE"})
        return {"decision": decision, "exact_duplicate": True}

    def route_after_deduplicate(state: GraphState) -> str:
        return END if state.get("exact_duplicate") else "triage"

    def node_gate(state: GraphState) -> dict:
        started(state["run_id"], "gate")
        repo.update_run_status(state["run_id"], "AWAITING_APPROVAL")
        decision_value = interrupt({"doc_id": state["document"].doc_id, "preview": state["triage"].preview_text})
        approved = decision_value == "approve"
        repo.update_run_status(state["run_id"], "RUNNING" if approved else "REJECTED_AT_GATE")
        record(state["run_id"], "gate", {"approved": approved})
        if not approved and on_stage is not None:
            on_stage(state["run_id"], "__done__", {"status": "REJECTED_AT_GATE"})
        return {"approved": approved}

    def route_after_gate(state: GraphState) -> str:
        return "extract" if state["approved"] else END

    def node_extract(state: GraphState) -> dict:
        started(state["run_id"], "extract")
        raw, extraction_path, confidence = route_and_extract(state["document"].content_path, extraction_client)
        record(state["run_id"], "extract", {"raw": raw, "extraction_path": extraction_path, "confidence": confidence})
        return {"raw_extraction": raw, "extraction_path": extraction_path, "extraction_confidence": confidence}

    def node_normalize(state: GraphState) -> dict:
        started(state["run_id"], "normalize")
        invoice = normalize_invoice(state["raw_extraction"], state["extraction_path"], state["extraction_confidence"])
        record(state["run_id"], "normalize", invoice.model_dump(mode="json"))
        return {"invoice": invoice}

    def node_validate(state: GraphState) -> dict:
        started(state["run_id"], "validate")
        invoice = validate_invoice(state["invoice"])
        record(state["run_id"], "validate", invoice.model_dump(mode="json"))
        return {"invoice": invoice}

    def node_vendor_resolve(state: GraphState) -> dict:
        started(state["run_id"], "vendor_resolve")
        vendor = resolve_vendor(state["invoice"].vendor_name_raw, repo.get_vendors())
        record(state["run_id"], "vendor_resolve", {"vendor": vendor.model_dump() if vendor else None})
        return {"vendor": vendor}

    def node_po_match(state: GraphState) -> dict:
        started(state["run_id"], "po_match")
        vendor = state.get("vendor")
        prior_invoices = repo.get_prior_invoices(vendor.vendor_id, state["run_id"]) if vendor is not None else []
        match = match_po(state["invoice"], vendor, repo.get_purchase_orders(), prior_invoices)
        record(state["run_id"], "po_match", match.model_dump(mode="json"))
        return {"match": match}

    def node_decide(state: GraphState) -> dict:
        started(state["run_id"], "decide")
        match = state["match"]
        decision = decide(state["invoice"], match)

        for _ in range(3):
            if decision.outcome != "AUTO_APPROVE" or match.po is None or match.comparison_amount is None:
                break
            new_invoiced_to_date = match.po.invoiced_to_date + match.comparison_amount
            if repo.update_po_balance_if_current(match.po.po_id, match.po.invoiced_to_date, new_invoiced_to_date):
                break
            vendor = state.get("vendor")
            prior_invoices = repo.get_prior_invoices(vendor.vendor_id, state["run_id"]) if vendor is not None else []
            match = match_po(state["invoice"], vendor, repo.get_purchase_orders(), prior_invoices)
            record(state["run_id"], "po_match_retry", match.model_dump(mode="json"))
            decision = decide(state["invoice"], match)
        else:
            decision = Decision(
                outcome="NEEDS_REVIEW",
                reason_codes=["CONCURRENT_PO_UPDATE"],
                explanation="Another invoice repeatedly changed this PO balance during finalization; manual review is required.",
                evidence=match.evidence,
            )

        record(state["run_id"], "decide", decision.model_dump(mode="json"))
        repo.set_run_decision(state["run_id"], decision)
        repo.update_run_status(state["run_id"], "DONE")
        vendor = state.get("vendor")
        if decision.outcome == "AUTO_APPROVE" and vendor is not None:
            repo.save_invoice_record(state["run_id"], vendor.vendor_id, state["invoice"])
        if on_stage is not None:
            on_stage(state["run_id"], "__done__", {"status": "DONE"})
        return {"decision": decision, "match": match}

    graph = StateGraph(GraphState)
    for name, fn in [
        ("deduplicate", node_deduplicate),
        ("triage", node_triage),
        ("gate", node_gate),
        ("extract", node_extract),
        ("normalize", node_normalize),
        ("validate", node_validate),
        ("vendor_resolve", node_vendor_resolve),
        ("po_match", node_po_match),
        ("decide", node_decide),
    ]:
        graph.add_node(name, fn)

    graph.set_entry_point("deduplicate")
    graph.add_conditional_edges("deduplicate", route_after_deduplicate, {"triage": "triage", END: END})
    graph.add_edge("triage", "gate")
    graph.add_conditional_edges("gate", route_after_gate, {"extract": "extract", END: END})
    graph.add_edge("extract", "normalize")
    graph.add_edge("normalize", "validate")
    graph.add_edge("validate", "vendor_resolve")
    graph.add_edge("vendor_resolve", "po_match")
    graph.add_edge("po_match", "decide")
    graph.add_edge("decide", END)

    checkpointer_ctx = closing(sqlite3.connect(checkpoint_db_path, check_same_thread=False))
    checkpoint_connection = checkpointer_ctx.__enter__()
    serializer = JsonPlusSerializer(
        allowed_msgpack_modules=[IncomingDocument, TriageResult, Invoice, VendorRecord, MatchResult, Decision]
    )
    checkpointer = SqliteSaver(checkpoint_connection, serde=serializer)
    compiled = graph.compile(checkpointer=checkpointer)
    compiled._checkpointer_ctx = checkpointer_ctx
    return compiled


def start_run(app: Any, run_id: str, document: IncomingDocument, repo: Repository) -> dict:
    repo.create_run(run_id, document.doc_id, "RUNNING", document.content_sha256)
    repo.append_stage(run_id, "intake", document.model_dump(mode="json"))
    config = {"configurable": {"thread_id": run_id}}
    return app.invoke({"document": document, "run_id": run_id}, config=config)


def resume_run(app: Any, run_id: str, approved: bool) -> dict:
    config = {"configurable": {"thread_id": run_id}}
    return app.invoke(Command(resume="approve" if approved else "reject"), config=config)
