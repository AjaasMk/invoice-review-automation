"""DeepSeek Chat Completions transport shared by triage and extraction."""

import os

import requests

from pipeline.extraction.nvidia_client import parse_chat_response

DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"
DEFAULT_DEEPSEEK_MODEL = "deepseek-flash"


def call_deepseek_chat(
    content: str | list,
    model: str = DEFAULT_DEEPSEEK_MODEL,
    api_key: str | None = None,
) -> str:
    resolved_key = api_key or os.environ.get("DEEPSEEK_API_KEY")
    if not resolved_key:
        raise ValueError("DEEPSEEK_API_KEY is not set; add it to .env before using DeepSeek")
    response = requests.post(
        DEEPSEEK_API_URL,
        headers={"Authorization": f"Bearer {resolved_key}", "Accept": "application/json"},
        json={
            "model": model,
            "messages": [{"role": "user", "content": content}],
            "max_tokens": 4096,
            "stream": False,
        },
        timeout=120,
    )
    if response.status_code != 200:
        raise RuntimeError(f"DeepSeek API request failed with status {response.status_code}: {response.text[:500]}")
    return parse_chat_response(response.json())
