from pipeline.extraction.nvidia_client import DEFAULT_NVIDIA_MODEL, build_request_payload


def test_kimi_payload_uses_supported_reasoning_settings() -> None:
    payload = build_request_payload("extract this", DEFAULT_NVIDIA_MODEL)
    assert payload["max_tokens"] == 16384
    assert payload["temperature"] == 1
    assert payload["top_p"] == 0.95
    assert payload["seed"] == 0
    assert payload["reasoning_effort"] == "max"
    assert payload["stream"] is False


def test_non_reasoning_fallback_keeps_deterministic_temperature() -> None:
    payload = build_request_payload("extract this", "meta/llama-3.2-11b-vision-instruct")
    assert payload["temperature"] == 0
    assert "reasoning_effort" not in payload
