from pathlib import Path

from pipeline.intake.folder_source import FolderSource


def test_poll_picks_up_supported_files_and_moves_them(tmp_path: Path) -> None:
    watch_dir = tmp_path / "watch"
    storage_dir = tmp_path / "storage"
    watch_dir.mkdir()
    (watch_dir / "invoice.pdf").write_bytes(b"%PDF-1.4 fake")
    (watch_dir / "notes.txt").write_text("ignore me")

    source = FolderSource(str(watch_dir), str(storage_dir))
    documents = source.poll()

    assert len(documents) == 1
    assert documents[0].source == "folder"
    assert documents[0].filename == "invoice.pdf"
    assert Path(documents[0].content_path).exists()
    assert not (watch_dir / "invoice.pdf").exists()
    assert (watch_dir / "notes.txt").exists()


def test_poll_does_not_return_the_same_file_twice(tmp_path: Path) -> None:
    watch_dir = tmp_path / "watch"
    storage_dir = tmp_path / "storage"
    watch_dir.mkdir()
    (watch_dir / "invoice.pdf").write_bytes(b"%PDF-1.4 fake")

    source = FolderSource(str(watch_dir), str(storage_dir))
    first = source.poll()
    second = source.poll()

    assert len(first) == 1
    assert len(second) == 0
