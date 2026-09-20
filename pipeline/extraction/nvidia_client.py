import base64
import os

import requests

NVIDIA_API_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
DEFAULT_NVIDIA_MODEL = "moonshotai/kimi-k3"


def build_text_message(instructions: str, text: str) -> str:
    return f"{instructions}\n\n{text}"


def build_image_message(instructions: str, image_bytes: bytes, media_type: str) -> list:
    encoded = base64.standard_b64encode(image_bytes).decode("utf-8")
    return [
        {"type": "text", "text": instructions},
        {"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{encoded}"}},
    ]


def parse_chat_response(body: dict) -> str:
    try:
        return body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError(f"unexpected NVIDIA response shape: {body!r}") from exc


def call_nvidia_chat(content: str | list, model: str = DEFAULT_NVIDIA_MODEL, api_key: str | None = None) -> str:
    resolved_key = api_key or os.environ.get("NVIDIA_API_KEY")
    if not resolved_key:
        raise ValueError("NVIDIA_API_KEY is not set; add it to .env before using the NVIDIA client")
    response = requests.post(
        NVIDIA_API_URL,
        headers={"Authorization": f"Bearer {resolved_key}", "Accept": "application/json"},
        json={
            "messages": [{"role": "user", "content": content}],
            "model": model,
            "max_tokens": 4096,
            "temperature": 0,
            "stream": False,
        },
        timeout=60,
    )
    if response.status_code != 200:
        raise RuntimeError(f"NVIDIA API request failed with status {response.status_code}: {response.text[:500]}")
    return parse_chat_response(response.json())
