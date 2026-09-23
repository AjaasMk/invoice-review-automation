import hashlib
from pathlib import Path

from pipeline.intake.upload_source import UploadSource


def test_save_writes_file_and_returns_document(tmp_path: Path) -> None:
    storage_dir = tmp_path / "storage"
    source = UploadSource(str(storage_dir))

    document = source.save("scan.png", b"fake png bytes")

    assert document.source == "upload"
    assert document.filename == "scan.png"
    assert Path(document.content_path).read_bytes() == b"fake png bytes"
    assert document.content_sha256 == hashlib.sha256(b"fake png bytes").hexdigest()
