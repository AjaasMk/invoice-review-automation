from typing import Any, Callable, TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, StateGraph
from langgraph.types import Command, interrupt

from pipeline.decision import decide
from pipeline.extraction.client import ExtractionClient
from pipeline.extraction.router import route_and_extract
from pipeline.normalize import normalize_invoice
from pipeline.po_match import match_po
from pipeline.schemas import Decision, IncomingDocument, Invoice, MatchResult, TriageResult, VendorRecord
from pipeline.storage import Repository
from pipeline.triage import TriageClient, run_triage
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


def build_graph(
    repo: Repository,
    extraction_client: ExtractionClient,
    triage_client: TriageClient,
    checkpoint_db_path: str,
    on_stage: Callable[[str, str, dict], None] | None = None,
) -> Any:
    def record(run_id: str, stage: str, payload: dict) -> None:
        repo.append_stage(run_id, stage, payload)
        if on_stage is not None:
            on_stage(run_id, stage, payload)

    def node_triage(state: GraphState) -> dict:
        triage_result = run_triage(state["document"].content_path, triage_client)
        record(state["run_id"], "triage", triage_result.model_dump())
        return {"triage": triage_result}

    def node_gate(state: GraphState) -> dict:
        repo.update_run_status(state["run_id"], "AWAITING_APPROVAL")
        decision_value = interrupt({"doc_id": state["document"].doc_id, "preview": state["triage"].preview_text})
        approved = decision_value == "approve"
        repo.update_run_status(state["run_id"], "RUNNING" if approved else "REJECTED_AT_GATE")
        record(state["run_id"], "gate", {"approved": approved})
        if not approved and on_stage is not None:
            on_stage(state["run_id"], "__done__", {})
        return {"approved": approved}

    def route_after_gate(state: GraphState) -> str:
        return "extract" if state["approved"] else END

    def node_extract(state: GraphState) -> dict:
        raw, extraction_path, confidence = route_and_extract(state["document"].content_path, extraction_client)
        record(state["run_id"], "extract", {"raw": raw, "extraction_path": extraction_path, "confidence": confidence})
        return {"raw_extraction": raw, "extraction_path": extraction_path, "extraction_confidence": confidence}

    def node_normalize(state: GraphState) -> dict:
        invoice = normalize_invoice(state["raw_extraction"], state["extraction_path"], state["extraction_confidence"])
        record(state["run_id"], "normalize", invoice.model_dump(mode="json"))
        return {"invoice": invoice}

    def node_validate(state: GraphState) -> dict:
        invoice = validate_invoice(state["invoice"])
        record(state["run_id"], "validate", invoice.model_dump(mode="json"))
        return {"invoice": invoice}

    def node_vendor_resolve(state: GraphState) -> dict:
        vendor = resolve_vendor(state["invoice"].vendor_name_raw, repo.get_vendors())
        record(state["run_id"], "vendor_resolve", {"vendor": vendor.model_dump() if vendor else None})
        if vendor is not None:
            repo.save_invoice_record(state["run_id"], vendor.vendor_id, state["invoice"])
        return {"vendor": vendor}

    def node_po_match(state: GraphState) -> dict:
        vendor = state.get("vendor")
        prior_invoices = repo.get_prior_invoices(vendor.vendor_id, state["run_id"]) if vendor is not None else []
        match = match_po(state["invoice"], vendor, repo.get_purchase_orders(), prior_invoices)
        record(state["run_id"], "po_match", match.model_dump(mode="json"))
        return {"match": match}

    def node_decide(state: GraphState) -> dict:
        decision = decide(state["invoice"], state["match"])
        record(state["run_id"], "decide", decision.model_dump(mode="json"))
        repo.set_run_decision(state["run_id"], decision)
        repo.update_run_status(state["run_id"], "DONE")
        match = state["match"]
        if decision.outcome == "AUTO_APPROVE" and match.po is not None and match.comparison_amount is not None:
            new_invoiced_to_date = match.po.invoiced_to_date + match.comparison_amount
            repo.update_po_balance(match.po.po_id, new_invoiced_to_date)
        if on_stage is not None:
            on_stage(state["run_id"], "__done__", {})
        return {"decision": decision}

    graph = StateGraph(GraphState)
    for name, fn in [
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

    graph.set_entry_point("triage")
    graph.add_edge("triage", "gate")
    graph.add_conditional_edges("gate", route_after_gate, {"extract": "extract", END: END})
    graph.add_edge("extract", "normalize")
    graph.add_edge("normalize", "validate")
    graph.add_edge("validate", "vendor_resolve")
    graph.add_edge("vendor_resolve", "po_match")
    graph.add_edge("po_match", "decide")
    graph.add_edge("decide", END)

    checkpointer_ctx = SqliteSaver.from_conn_string(checkpoint_db_path)
    checkpointer = checkpointer_ctx.__enter__()
    compiled = graph.compile(checkpointer=checkpointer)
    compiled._checkpointer_ctx = checkpointer_ctx
    return compiled


def start_run(app: Any, run_id: str, document: IncomingDocument, repo: Repository) -> dict:
    repo.create_run(run_id, document.doc_id, "RUNNING")
    config = {"configurable": {"thread_id": run_id}}
    return app.invoke({"document": document, "run_id": run_id}, config=config)


def resume_run(app: Any, run_id: str, approved: bool) -> dict:
    config = {"configurable": {"thread_id": run_id}}
    return app.invoke(Command(resume="approve" if approved else "reject"), config=config)
