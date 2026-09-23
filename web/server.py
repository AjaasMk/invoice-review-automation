import asyncio
import json
import logging
import os
import secrets
import sqlite3
import uuid
from contextlib import asynccontextmanager
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, Header, HTTPException, UploadFile
from pydantic import BaseModel, Field
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

from pipeline.events import EventBus
from pipeline.graph import build_graph, resume_run, start_run
from pipeline.intake.folder_source import FolderSource
from pipeline.intake.imap_source import ImapSource
from pipeline.intake.upload_source import UploadSource
from pipeline.storage import Repository
from pipeline.schemas import Decision, Invoice, PurchaseOrder, VendorRecord
from pipeline.decision import decide
from pipeline.po_match import match_po
from pipeline.vendor_resolve import resolve_vendor

logger = logging.getLogger("invoice_pipeline.web")
SUPPORTED_UPLOAD_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg"}
MAX_UPLOAD_BYTES = 15 * 1024 * 1024


class ExceptionResolutionRequest(BaseModel):
    action: Literal[
        "add_vendor", "create_purchase_order", "select_purchase_order", "correct_fields",
        "approve_exception", "reject_invoice", "archive_invoice", "hold_for_procurement",
        "confirm_duplicate", "mark_distinct", "recheck_invoice",
    ]
    note: str | None = Field(default=None, max_length=1000)
    vendor_name: str | None = Field(default=None, max_length=200)
    vendor_aliases: list[str] = Field(default_factory=list)
    vendor_approved: bool = False
    selected_vendor_id: str | None = None
    selected_po_id: str | None = None
    po_id: str | None = Field(default=None, max_length=100)
    po_amount: Decimal | None = Field(default=None, gt=0)
    po_currency: str = Field(default="USD", min_length=3, max_length=3)
    po_issued_date: date | None = None
    po_tax_treatment: Literal["inclusive", "exclusive"] = "exclusive"
    corrected_fields: dict[str, str] = Field(default_factory=dict)


def create_app(
    repo: Repository,
    extraction_client: Any,
    triage_client: Any,
    folder_source: FolderSource,
    upload_source: UploadSource,
    checkpoint_db_path: str,
    imap_source: ImapSource | None = None,
    poll_interval_seconds: float = 5.0,
) -> FastAPI:
    async def run_resume_safely(run_id: str, approved: bool) -> None:
        async with app.state.state_lock:
            try:
                await asyncio.to_thread(resume_run, app.state.graph, run_id, approved)
            except Exception as exc:
                logger.exception(f"pipeline run {run_id} failed after gate decision (approved={approved}): {exc}")
                repo.update_run_status(run_id, "ERROR")
                payload = {"event": "failed", "error_type": type(exc).__name__, "message": str(exc)}
                repo.append_stage(run_id, "error", payload)
                app.state.bus.publish(run_id, "error", payload)
                app.state.bus.publish(run_id, "__done__", {"status": "ERROR"})

    async def run_start_safely(run_id: str, document: Any) -> None:
        async with app.state.state_lock:
            try:
                await asyncio.to_thread(start_run, app.state.graph, run_id, document, repo)
            except Exception as exc:
                logger.exception(f"pipeline run {run_id} failed during intake/triage: {exc}")
                if repo.get_run(run_id) is not None:
                    repo.update_run_status(run_id, "ERROR")
                    payload = {"event": "failed", "error_type": type(exc).__name__, "message": str(exc)}
                    repo.append_stage(run_id, "error", payload)
                    app.state.bus.publish(run_id, "error", payload)
                    app.state.bus.publish(run_id, "__done__", {"status": "ERROR"})

    async def poll_loop() -> None:
        while True:
            try:
                documents = folder_source.poll()
                if imap_source is not None:
                    documents = documents + imap_source.poll()
                for document in documents:
                    run_id = str(uuid.uuid4())
                    await run_start_safely(run_id, document)
            except Exception as exc:
                logger.exception(f"intake poll loop failed: {exc}")
            await asyncio.sleep(poll_interval_seconds)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        loop = asyncio.get_running_loop()
        app.state.bus = EventBus(loop)
        app.state.state_lock = asyncio.Lock()
        app.state.graph = build_graph(repo, extraction_client, triage_client, checkpoint_db_path, on_stage=app.state.bus.publish)
        app.state.poll_task = asyncio.create_task(poll_loop())
        try:
            yield
        finally:
            app.state.poll_task.cancel()
            try:
                await app.state.poll_task
            except asyncio.CancelledError:
                pass
            app.state.graph._checkpointer_ctx.__exit__(None, None, None)

    app = FastAPI(lifespan=lifespan)

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/api/intake/status")
    def intake_status() -> dict:
        if imap_source is None:
            return {
                "enabled": False,
                "unclassified_unread_attachments": 0,
                "awaiting_human_triage": 0,
                "last_poll_at": None,
                "last_error": None,
            }
        return imap_source.status()

    @app.get("/api/workspace")
    def workspace() -> dict:
        """Read-only reference data and explicitly allowlisted runtime metadata."""
        orders = repo.get_purchase_orders()
        return {
            "vendors": [vendor.model_dump(mode="json") for vendor in repo.get_vendors()],
            "purchase_orders": [
                {**order.model_dump(mode="json"),
                 "remaining_balance": str(order.amount - order.invoiced_to_date)}
                for order in orders
            ],
            "runtime": {
                "extraction_client": type(extraction_client).__name__,
                "model": getattr(extraction_client, "_model", None),
                "email_enabled": imap_source is not None,
                "poll_interval_seconds": poll_interval_seconds,
            },
        }

    @app.post("/api/demo/reset")
    async def reset_demo_state(confirm: bool = False, x_demo_reset_token: str | None = Header(default=None)) -> dict:
        if not confirm:
            raise HTTPException(status_code=400, detail="set confirm=true to reset the demo state")
        expected_reset_token = os.environ.get("DEMO_RESET_TOKEN")
        if expected_reset_token and not secrets.compare_digest(x_demo_reset_token or "", expected_reset_token):
            raise HTTPException(status_code=403, detail="a valid demo reset token is required")
        async with app.state.state_lock:
            summary = await asyncio.to_thread(repo.reset_demo_state)
            await asyncio.to_thread(_clear_checkpoints, checkpoint_db_path)
        return {**summary, "po_balances_reset": True, "checkpoints_cleared": True}

    @app.post("/api/upload")
    async def upload(file: UploadFile) -> dict:
        filename = file.filename or ""
        if Path(filename).suffix.lower() not in SUPPORTED_UPLOAD_EXTENSIONS:
            raise HTTPException(status_code=415, detail="upload a PDF, PNG, JPG, or JPEG invoice")
        content = await file.read()
        if not content:
            raise HTTPException(status_code=400, detail="the uploaded file is empty")
        if len(content) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="the uploaded file exceeds the 15 MB limit")
        document = upload_source.save(filename, content)
        run_id = str(uuid.uuid4())
        await run_start_safely(run_id, document)
        return {"run_id": run_id, "doc_id": document.doc_id}

    @app.get("/api/gate/pending")
    def gate_pending() -> list[dict]:
        pending = [run for run in repo.list_runs() if run["status"] == "AWAITING_APPROVAL"]
        result = []
        for run in pending:
            stages = repo.get_stages(run["run_id"])
            triage_stage = next((s for s in stages if s["stage"] == "triage"), None)
            result.append({**run, "triage": triage_stage["payload"] if triage_stage else None})
        return result

    @app.post("/api/gate/{run_id}/approve")
    async def approve(run_id: str) -> dict:
        _require_pending_run(repo, run_id)
        asyncio.create_task(run_resume_safely(run_id, True))
        return {"status": "started"}

    @app.post("/api/gate/{run_id}/reject")
    async def reject(run_id: str) -> dict:
        _require_pending_run(repo, run_id)
        asyncio.create_task(run_resume_safely(run_id, False))
        return {"status": "started"}

    @app.get("/api/runs")
    def list_runs() -> list[dict]:
        return repo.list_runs()

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str) -> dict:
        run = repo.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"run {run_id} not found")
        return {**run, "stages": repo.get_stages(run_id)}

    @app.post("/api/runs/{run_id}/exception-resolution")
    async def resolve_exception(run_id: str, request: ExceptionResolutionRequest) -> dict:
        """Record a human exception decision without erasing the automated evidence."""
        run = repo.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"run {run_id} not found")
        if (run.get("decision") or {}).get("outcome") != "NEEDS_REVIEW":
            raise HTTPException(status_code=409, detail="only NEEDS_REVIEW invoices can be resolved here")

        payload = request.model_dump(mode="json", exclude_none=True)
        action = request.action
        resolution_status = "OPEN"

        if action == "add_vendor":
            if not request.vendor_name:
                raise HTTPException(status_code=422, detail="vendor_name is required when adding a vendor")
            vendor = VendorRecord(
                vendor_id=f"VND-{uuid.uuid4().hex[:8].upper()}",
                canonical_name=request.vendor_name.strip(),
                aliases=[alias.strip() for alias in request.vendor_aliases if alias.strip()],
                approved=request.vendor_approved,
            )
            try:
                await asyncio.to_thread(repo.add_vendor, vendor)
            except sqlite3.IntegrityError as exc:
                raise HTTPException(status_code=409, detail="a vendor with that identifier already exists") from exc
            payload["created_vendor"] = vendor.model_dump(mode="json")
            resolution_status = "REFERENCE_ADDED"

        elif action == "create_purchase_order":
            if not all([request.selected_vendor_id, request.po_id, request.po_amount, request.po_issued_date]):
                raise HTTPException(status_code=422, detail="vendor, PO number, amount, and issued date are required")
            if request.selected_vendor_id not in {vendor.vendor_id for vendor in repo.get_vendors()}:
                raise HTTPException(status_code=422, detail="select an existing vendor before creating a purchase order")
            purchase_order = PurchaseOrder(
                po_id=request.po_id.strip(),
                vendor_id=request.selected_vendor_id,
                amount=request.po_amount,
                currency=request.po_currency.upper(),
                issued_date=request.po_issued_date,
                tax_treatment=request.po_tax_treatment,
            )
            try:
                await asyncio.to_thread(repo.add_purchase_order, purchase_order)
            except sqlite3.IntegrityError as exc:
                raise HTTPException(status_code=409, detail="that purchase-order number already exists") from exc
            payload["created_purchase_order"] = purchase_order.model_dump(mode="json")
            resolution_status = "REFERENCE_ADDED"

        elif action == "select_purchase_order":
            if not request.selected_po_id:
                raise HTTPException(status_code=422, detail="select a purchase order")
            if request.selected_po_id not in {order.po_id for order in repo.get_purchase_orders()}:
                raise HTTPException(status_code=422, detail="the selected purchase order no longer exists")
            resolution_status = "MATCH_ASSIGNED"

        elif action == "correct_fields":
            latest_invoice_payload = next(
                (stage["payload"] for stage in reversed(repo.get_stages(run_id)) if stage["stage"] in {"manual_correction", "validate"}),
                None,
            )
            if latest_invoice_payload is None:
                raise HTTPException(status_code=409, detail="this run has no extracted invoice fields to correct")
            if not request.corrected_fields:
                raise HTTPException(status_code=422, detail="enter at least one corrected field")
            corrected_invoice = Invoice.model_validate({**latest_invoice_payload, **request.corrected_fields})
            payload["corrected_invoice"] = corrected_invoice.model_dump(mode="json")
            await asyncio.to_thread(repo.append_stage, run_id, "manual_correction", payload["corrected_invoice"])
            resolution_status = "CORRECTION_RECORDED"

        elif action == "recheck_invoice":
            latest_invoice_payload = next(
                (stage["payload"] for stage in reversed(repo.get_stages(run_id)) if stage["stage"] in {"manual_correction", "validate"}),
                None,
            )
            if latest_invoice_payload is None:
                raise HTTPException(status_code=409, detail="this run has no extracted invoice fields to re-check")
            invoice = Invoice.model_validate(latest_invoice_payload)
            vendor = resolve_vendor(invoice.vendor_name_raw, repo.get_vendors())
            prior_invoices = repo.get_prior_invoices(vendor.vendor_id, run_id) if vendor else []
            match = match_po(invoice, vendor, repo.get_purchase_orders(), prior_invoices)
            decision = decide(invoice, match)
            if decision.outcome == "AUTO_APPROVE" and match.po is not None and match.comparison_amount is not None:
                new_balance = match.po.invoiced_to_date + match.comparison_amount
                if not repo.update_po_balance_if_current(match.po.po_id, match.po.invoiced_to_date, new_balance):
                    decision = Decision(
                        outcome="NEEDS_REVIEW",
                        reason_codes=["CONCURRENT_PO_UPDATE"],
                        explanation="The PO balance changed while this invoice was being re-checked; review it again.",
                        evidence=match.evidence,
                    )
                elif vendor is not None:
                    repo.save_invoice_record(run_id, vendor.vendor_id, invoice)
            await asyncio.to_thread(repo.append_stage, run_id, "manual_vendor_resolve", {"vendor": vendor.model_dump(mode="json") if vendor else None})
            await asyncio.to_thread(repo.append_stage, run_id, "manual_po_match", match.model_dump(mode="json"))
            await asyncio.to_thread(repo.append_stage, run_id, "manual_decide", decision.model_dump(mode="json"))
            await asyncio.to_thread(repo.set_run_decision, run_id, decision)
            payload["rechecked_outcome"] = decision.outcome
            resolution_status = "CLOSED" if decision.outcome != "NEEDS_REVIEW" else "OPEN"

        elif action in {"approve_exception", "mark_distinct"}:
            resolution_status = "CLOSED"
        elif action in {"reject_invoice", "confirm_duplicate"}:
            existing = run["decision"]
            rejected = Decision(
                outcome="REJECT",
                reason_codes=[*existing.get("reason_codes", []), "MANUAL_REJECTION"],
                explanation=f"{existing.get('explanation', 'Invoice needs review.')} Human resolution: {action.replace('_', ' ')}.",
                evidence=existing.get("evidence", []),
            )
            await asyncio.to_thread(repo.set_run_decision, run_id, rejected)
            resolution_status = "CLOSED"
        elif action == "archive_invoice":
            resolution_status = "ARCHIVED"
        elif action == "hold_for_procurement":
            resolution_status = "ON_HOLD"

        resolution = await asyncio.to_thread(
            repo.set_exception_resolution, run_id, resolution_status, action, request.note, payload
        )
        await asyncio.to_thread(
            repo.append_stage,
            run_id,
            "exception_resolution",
            {"action": action, "status": resolution_status, "note": request.note, "payload": payload},
        )
        return {"resolution": resolution, "run": repo.get_run(run_id)}

    @app.get("/api/runs/{run_id}/events")
    async def run_events(run_id: str) -> StreamingResponse:
        run = repo.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"run {run_id} not found")

        async def event_stream():
            latest = repo.get_run(run_id)
            if latest is not None and latest["status"] in {"DONE", "ERROR", "REJECTED_AT_GATE"}:
                yield f"data: {json.dumps({'stage': '__done__', 'payload': {'status': latest['status']}})}\n\n"
                return
            async for event in app.state.bus.subscribe(run_id):
                yield f"data: {json.dumps(event)}\n\n"

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    app.mount("/", StaticFiles(directory="web/static", html=True), name="static")
    return app


def _require_pending_run(repo: Repository, run_id: str) -> None:
    run = repo.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    if run["status"] != "AWAITING_APPROVAL":
        raise HTTPException(status_code=409, detail=f"run {run_id} is {run['status']}, not awaiting approval")


def _clear_checkpoints(checkpoint_db_path: str) -> None:
    with sqlite3.connect(checkpoint_db_path) as conn:
        for table in ("writes", "checkpoints"):
            conn.execute(f"DELETE FROM {table}")


def _load_reference_data(repo: Repository) -> None:
    from pathlib import Path

    from pipeline.schemas import PurchaseOrder, VendorRecord

    data_dir = Path(__file__).resolve().parent.parent / "data"
    vendors = [VendorRecord.model_validate(v) for v in json.loads((data_dir / "vendors.json").read_text())]
    purchase_orders = [PurchaseOrder.model_validate(p) for p in json.loads((data_dir / "purchase_orders.json").read_text())]
    repo.seed_reference_data(vendors, purchase_orders)


def _build_llm_clients() -> tuple[object, object]:
    import os

    from pipeline.extraction.vision import AnthropicExtractionClient, DeepSeekExtractionClient, NvidiaExtractionClient
    from pipeline.triage import AnthropicTriageClient, DeepSeekTriageClient, NvidiaTriageClient

    provider = os.environ.get("LLM_PROVIDER", "").strip().lower()
    if not provider:
        if os.environ.get("DEEPSEEK_API_KEY"):
            provider = "deepseek"
        elif os.environ.get("NVIDIA_API_KEY"):
            provider = "nvidia"
        elif os.environ.get("ANTHROPIC_API_KEY"):
            provider = "anthropic"
        else:
            raise ValueError("set DEEPSEEK_API_KEY, NVIDIA_API_KEY, or ANTHROPIC_API_KEY in .env before running")

    if provider == "deepseek":
        if not os.environ.get("DEEPSEEK_API_KEY"):
            raise ValueError("LLM_PROVIDER=deepseek requires DEEPSEEK_API_KEY in .env")
        model = os.environ.get("DEEPSEEK_MODEL") or "deepseek-flash"
        logger.info(f"LLM provider: deepseek (model={model})")
        return DeepSeekExtractionClient(model=model), DeepSeekTriageClient(model=model)

    if provider == "nvidia":
        model = os.environ.get("NVIDIA_MODEL") or None
        kwargs = {"model": model} if model else {}
        logger.info(f"LLM provider: nvidia (model={model or 'default'})")
        return NvidiaExtractionClient(**kwargs), NvidiaTriageClient(**kwargs)

    if provider == "anthropic":
        logger.info("LLM provider: anthropic")
        return AnthropicExtractionClient(), AnthropicTriageClient()

    raise ValueError(f"unknown LLM_PROVIDER '{provider}'; use 'deepseek', 'nvidia', or 'anthropic'")


def _build_real_app() -> FastAPI:
    import os

    from dotenv import load_dotenv

    load_dotenv()

    extraction_client, triage_client = _build_llm_clients()

    repo = Repository("runs/app.db")
    repo.init_db()
    _load_reference_data(repo)

    folder_source = FolderSource("runs/inbox", "runs/intake_storage")
    upload_source = UploadSource("runs/uploads")

    imap_source = None
    imap_user = os.environ.get("GMAIL_IMAP_USER")
    imap_password = os.environ.get("GMAIL_IMAP_APP_PASSWORD")
    if imap_user and imap_password:
        from pipeline.intake.imap_source import RealImapClient

        imap_host = os.environ.get("GMAIL_IMAP_HOST", "imap.gmail.com")
        imap_mailbox = os.environ.get("GMAIL_IMAP_MAILBOX", "INBOX")
        imap_client = RealImapClient(imap_host, imap_user, imap_password)
        imap_source = ImapSource(imap_client, "runs/intake_storage", mailbox=imap_mailbox, repository=repo)
        logger.info(f"IMAP polling enabled for {imap_user}@{imap_host}, mailbox={imap_mailbox}")
    else:
        logger.info("IMAP polling disabled (GMAIL_IMAP_USER/GMAIL_IMAP_APP_PASSWORD not set) — use folder/upload intake")

    return create_app(
        repo,
        extraction_client,
        triage_client,
        folder_source,
        upload_source,
        "runs/checkpoints.db",
        imap_source=imap_source,
        poll_interval_seconds=5.0,
    )


if __name__ == "__main__":
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(_build_real_app(), host="0.0.0.0", port=port)
