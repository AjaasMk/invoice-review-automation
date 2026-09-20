import base64
import os
from pathlib import Path
from typing import Protocol

from anthropic import Anthropic

from pipeline.extraction.json_parsing import parse_json_response
from pipeline.extraction.pdf_text import extract_raw_text, has_text_layer
from pipeline.extraction.router import rasterize_first_page
from pipeline.extraction.vision import DEFAULT_MODEL, text_from_response
from pipeline.schemas import TriageResult

TRIAGE_INSTRUCTIONS = (
    "Look at this document and decide whether it is a vendor invoice. Return "
    "JSON with exactly these keys: looks_like_invoice (boolean), preview_text "
    "(a one or two sentence human-readable summary of what this document is), "
    "reason (why you decided that). Return only the JSON object, no prose."
)

IMAGE_MEDIA_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}


class TriageClient(Protocol):
    def triage_text(self, text: str) -> dict: ...
    def triage_image(self, image_bytes: bytes, media_type: str) -> dict: ...


def run_triage(file_path: str, client: TriageClient) -> TriageResult:
    path = Path(file_path)
    suffix = path.suffix.lower()

    if suffix in IMAGE_MEDIA_TYPES:
        raw = client.triage_image(path.read_bytes(), IMAGE_MEDIA_TYPES[suffix])
        return TriageResult.model_validate(raw)

    if suffix == ".pdf" and has_text_layer(file_path):
        raw = client.triage_text(extract_raw_text(file_path))
        return TriageResult.model_validate(raw)

    if suffix == ".pdf":
        raw = client.triage_image(rasterize_first_page(file_path), "image/png")
        return TriageResult.model_validate(raw)

    raise ValueError(f"unsupported attachment type for triage: {suffix}")


class AnthropicTriageClient:
    def __init__(self, api_key: str | None = None, model: str = DEFAULT_MODEL) -> None:
        resolved_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not resolved_key:
            raise ValueError("ANTHROPIC_API_KEY is not set; add it to .env before running triage")
        self._client = Anthropic(api_key=resolved_key)
        self._model = model

    def triage_text(self, text: str) -> dict:
        response = self._client.messages.create(
            model=self._model,
            max_tokens=512,
            messages=[{"role": "user", "content": f"{TRIAGE_INSTRUCTIONS}\n\nDocument text:\n{text}"}],
        )
        return parse_json_response(text_from_response(response))

    def triage_image(self, image_bytes: bytes, media_type: str) -> dict:
        encoded = base64.standard_b64encode(image_bytes).decode("utf-8")
        response = self._client.messages.create(
            model=self._model,
            max_tokens=512,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": encoded}},
                        {"type": "text", "text": TRIAGE_INSTRUCTIONS},
                    ],
                }
            ],
        )
        return parse_json_response(text_from_response(response))
