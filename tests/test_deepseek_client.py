import pytest

from pipeline.extraction import deepseek_client
from pipeline.extraction.vision import DeepSeekExtractionClient
from pipeline.triage import DeepSeekTriageClient
from web.server import _build_llm_clients


class FakeResponse:
    status_code = 200

    def json(self) -> dict:
        return {"choices": [{"message": {"content": '{"looks_like_invoice": true, "preview_text": "Invoice", "reason": "has a total"}'}}]}


def test_deepseek_transport_uses_configured_key_and_model(monkeypatch) -> None:
    sent = {}

    def fake_post(url, **kwargs):
        sent.update({"url": url, **kwargs})
        return FakeResponse()

    monkeypatch.setattr(deepseek_client.requests, "post", fake_post)
    result = deepseek_client.call_deepseek_chat("invoice text", api_key="test-key")
    assert '"looks_like_invoice": true' in result
    assert sent["url"] == "https://api.deepseek.com/chat/completions"
    assert sent["headers"]["Authorization"] == "Bearer test-key"
    assert sent["json"]["model"] == "deepseek-flash"
    assert sent["json"]["messages"] == [{"role": "user", "content": "invoice text"}]
    assert sent["json"]["stream"] is False


def test_deepseek_triage_sends_image_blocks(monkeypatch) -> None:
    captured = {}

    def fake_call(content, model, api_key):
        captured.update({"content": content, "model": model, "api_key": api_key})
        return '{"looks_like_invoice": true, "preview_text": "Invoice", "reason": "has a total"}'

    monkeypatch.setattr(deepseek_client, "call_deepseek_chat", fake_call)
    result = DeepSeekTriageClient(api_key="test-key").triage_image(b"image", "image/png")
    assert result["looks_like_invoice"] is True
    assert captured["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_deepseek_extraction_sends_multiple_pages(monkeypatch) -> None:
    captured = {}

    def fake_call(content, model, api_key):
        captured["content"] = content
        return '{"invoice_number": "INV-1", "vendor_name": "Acme Corp"}'

    monkeypatch.setattr(deepseek_client, "call_deepseek_chat", fake_call)
    result = DeepSeekExtractionClient(api_key="test-key").structure_from_images(
        [(b"page 1", "image/png"), (b"page 2", "image/png")]
    )
    assert result["invoice_number"] == "INV-1"
    assert len([block for block in captured["content"] if block["type"] == "image_url"]) == 2


def test_provider_selection_requires_key(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(ValueError, match="DEEPSEEK_API_KEY"):
        _build_llm_clients()


def test_provider_selection_uses_deepseek_when_key_present(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    extraction, triage = _build_llm_clients()
    assert isinstance(extraction, DeepSeekExtractionClient)
    assert isinstance(triage, DeepSeekTriageClient)
