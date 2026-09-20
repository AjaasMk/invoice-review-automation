import asyncio
import json
import logging
import uuid
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
    app = FastAPI()

    async def run_resume_safely(run_id: str, approved: bool) -> None:
        try:
            await asyncio.to_thread(resume_run, app.state.graph, run_id, approved)
        except Exception as exc:
            logger.exception(f"pipeline run {run_id} failed after gate decision (approved={approved}): {exc}")
            repo.update_run_status(run_id, "ERROR")

    async def run_start_safely(run_id: str, document: Any) -> None:
        try:
            await asyncio.to_thread(start_run, app.state.graph, run_id, document, repo)
        except Exception as exc:
            logger.exception(f"pipeline run {run_id} failed during intake/triage: {exc}")
            repo.update_run_status(run_id, "ERROR")

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

    @app.on_event("startup")
    async def startup() -> None:
        loop = asyncio.get_running_loop()
        app.state.bus = EventBus(loop)
        app.state.graph = build_graph(repo, extraction_client, triage_client, checkpoint_db_path, on_stage=app.state.bus.publish)
        app.state.poll_task = asyncio.create_task(poll_loop())

    @app.on_event("shutdown")
    async def shutdown() -> None:
        app.state.poll_task.cancel()

    @app.post("/api/upload")
    async def upload(file: UploadFile) -> dict:
        content = await file.read()
        document = upload_source.save(file.filename, content)
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
        asyncio.create_task(run_resume_safely(run_id, True))
        return {"status": "started"}

    @app.post("/api/gate/{run_id}/reject")
    async def reject(run_id: str) -> dict:
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
        async def event_stream():
            async for event in app.state.bus.subscribe(run_id):
                yield f"data: {json.dumps(event)}\n\n"

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    app.mount("/", StaticFiles(directory="web/static", html=True), name="static")
    return app
