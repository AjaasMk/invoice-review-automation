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


def test_invalid_json_returns_fallback_when_provided_instead_of_raising() -> None:
    fallback = {"invoice_number": None, "vendor_name": None}
    result = parse_json_response("Sure, here is a summary of the image: it is blurry.", fallback=fallback)
    assert result == fallback


def test_fallback_returns_a_copy_not_the_same_object() -> None:
    fallback = {"invoice_number": None}
    result = parse_json_response("not json", fallback=fallback)
    result["invoice_number"] = "mutated"
    assert fallback["invoice_number"] is None
