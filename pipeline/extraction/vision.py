import base64
import os

from anthropic import Anthropic

from pipeline.extraction import nvidia_client
from pipeline.extraction.client import EMPTY_EXTRACTION_RESULT, EXTRACTION_INSTRUCTIONS
from pipeline.extraction.json_parsing import parse_json_response

DEFAULT_MODEL = "claude-sonnet-5"


class AnthropicExtractionClient:
    def __init__(self, api_key: str | None = None, model: str = DEFAULT_MODEL) -> None:
        resolved_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not resolved_key:
            raise ValueError("ANTHROPIC_API_KEY is not set; add it to .env before running extraction")
        self._client = Anthropic(api_key=resolved_key)
        self._model = model

    def structure_from_text(self, text: str) -> dict:
        response = self._client.messages.create(
            model=self._model,
            max_tokens=1024,
            messages=[{"role": "user", "content": f"{EXTRACTION_INSTRUCTIONS}\n\nInvoice text:\n{text}"}],
        )
        return parse_json_response(text_from_response(response), fallback=EMPTY_EXTRACTION_RESULT)

    def structure_from_image(self, image_bytes: bytes, media_type: str) -> dict:
        encoded = base64.standard_b64encode(image_bytes).decode("utf-8")
        response = self._client.messages.create(
            model=self._model,
            max_tokens=1024,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": encoded}},
                        {"type": "text", "text": EXTRACTION_INSTRUCTIONS},
                    ],
                }
            ],
        )
        return parse_json_response(text_from_response(response), fallback=EMPTY_EXTRACTION_RESULT)


def text_from_response(response: object) -> str:
    return "".join(block.text for block in response.content if block.type == "text")


class NvidiaExtractionClient:
    def __init__(self, api_key: str | None = None, model: str = nvidia_client.DEFAULT_NVIDIA_MODEL) -> None:
        self._api_key = api_key
        self._model = model

    def structure_from_text(self, text: str) -> dict:
        content = nvidia_client.build_text_message(EXTRACTION_INSTRUCTIONS, text)
        raw_text = nvidia_client.call_nvidia_chat(content, self._model, self._api_key)
        return parse_json_response(raw_text, fallback=EMPTY_EXTRACTION_RESULT)

    def structure_from_image(self, image_bytes: bytes, media_type: str) -> dict:
        content = nvidia_client.build_image_message(EXTRACTION_INSTRUCTIONS, image_bytes, media_type)
        raw_text = nvidia_client.call_nvidia_chat(content, self._model, self._api_key)
        return parse_json_response(raw_text, fallback=EMPTY_EXTRACTION_RESULT)
