from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parent.parent / "web" / "static"


def test_index_references_existing_static_files() -> None:
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    assert "/styles.css" in html
    assert "/app.js" in html
    assert (STATIC_DIR / "styles.css").exists()
    assert (STATIC_DIR / "app.js").exists()


def test_app_js_calls_every_server_endpoint_from_task_15() -> None:
    js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    for endpoint in ["/api/upload", "/api/gate/pending", "/api/runs", "/api/demo/reset", "/events"]:
        assert endpoint in js
