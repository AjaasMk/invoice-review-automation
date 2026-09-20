import pytest

from pipeline.extraction.json_parsing import parse_json_response


def test_parses_plain_json() -> None:
    result = parse_json_response('{"invoice_number": "INV-1"}')
    assert result == {"invoice_number": "INV-1"}


def test_strips_markdown_json_fence() -> None:
    result = parse_json_response('```json\n{"invoice_number": "INV-1"}\n```')
    assert result == {"invoice_number": "INV-1"}


def test_strips_plain_markdown_fence() -> None:
    result = parse_json_response('```\n{"invoice_number": "INV-1"}\n```')
    assert result == {"invoice_number": "INV-1"}


def test_invalid_json_raises_value_error_with_context() -> None:
    with pytest.raises(ValueError, match="did not return valid JSON"):
        parse_json_response("not json at all")
