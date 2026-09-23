import asyncio
import json
import logging
import os
import sqlite3
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

from pipeline.events import EventBus
from pipeline.graph import build_graph, resume_run, start_run
from pipeline.intake.folder_source import FolderSource
from pipeline.intake.imap_source import ImapSource
from pipeline.intake.upload_source import UploadSource
from pipeline.storage import Repository

logger = logging.getLogger("invoice_pipeline.web")
SUPPORTED_UPLOAD_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg"}
MAX_UPLOAD_BYTES = 15 * 1024 * 1024


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
    async def reset_demo_state(confirm: bool = False) -> dict:
        if not confirm:
            raise HTTPException(status_code=400, detail="set confirm=true to reset the demo state")
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
