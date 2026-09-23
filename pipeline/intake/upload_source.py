import hashlib
import uuid
from datetime import datetime, timezone
from pathlib import Path

from pipeline.schemas import IncomingDocument


class UploadSource:
    def __init__(self, storage_dir: str) -> None:
        self._storage_dir = Path(storage_dir)
        self._storage_dir.mkdir(parents=True, exist_ok=True)

    def save(self, filename: str, content: bytes) -> IncomingDocument:
        doc_id = str(uuid.uuid4())
        suffix = Path(filename).suffix.lower()
        destination = self._storage_dir / f"{doc_id}{suffix}"
        destination.write_bytes(content)
        return IncomingDocument(
            doc_id=doc_id,
            source="upload",
            received_at=datetime.now(timezone.utc),
            sender=None,
            filename=filename,
            content_path=str(destination),
            content_sha256=hashlib.sha256(content).hexdigest(),
        )
