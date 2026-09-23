import json


def parse_json_response(raw_text: str, fallback: dict | None = None) -> dict:
    cleaned = _strip_markdown_fence(raw_text.strip())
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        if fallback is not None:
            return dict(fallback)
        raise ValueError(f"model did not return valid JSON: {cleaned[:200]!r}") from exc


def _strip_markdown_fence(text: str) -> str:
    if not text.startswith("```"):
        return text
    lines = text.splitlines()[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()
