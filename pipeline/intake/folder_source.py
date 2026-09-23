import hashlib
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from pipeline.schemas import IncomingDocument

SUPPORTED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg"}


class FolderSource:
    def __init__(self, watch_dir: str, storage_dir: str) -> None:
        self._watch_dir = Path(watch_dir)
        self._storage_dir = Path(storage_dir)
        self._watch_dir.mkdir(parents=True, exist_ok=True)
        self._storage_dir.mkdir(parents=True, exist_ok=True)

    def poll(self) -> list[IncomingDocument]:
        documents: list[IncomingDocument] = []
        for path in sorted(self._watch_dir.iterdir()):
            if not path.is_file() or path.suffix.lower() not in SUPPORTED_EXTENSIONS:
                continue
            doc_id = str(uuid.uuid4())
            destination = self._storage_dir / f"{doc_id}{path.suffix.lower()}"
            content_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
            shutil.move(str(path), str(destination))
            documents.append(
                IncomingDocument(
                    doc_id=doc_id,
                    source="folder",
                    received_at=datetime.now(timezone.utc),
                    sender=None,
                    filename=path.name,
                    content_path=str(destination),
                    content_sha256=content_sha256,
                )
            )
        return documents
