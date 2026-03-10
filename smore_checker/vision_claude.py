"""Thin provider module for Claude vision/text API calls."""

import base64
import anthropic

PROVIDER_NAME = "Claude Sonnet"
MODEL = "claude-sonnet-4-20250514"
RATE_LIMIT_DELAY = 0

_client = None


def get_client() -> anthropic.Anthropic:
    """Lazy-init and return the Anthropic client."""
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


def call_vision(image_bytes: bytes, mime_type: str, prompt: str, max_tokens: int = 1024) -> str:
    """Single-image vision call. Returns response text."""
    client = get_client()
    b64_data = base64.standard_b64encode(image_bytes).decode("ascii")
    response = client.messages.create(
        model=MODEL,
        max_tokens=max_tokens,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": mime_type, "data": b64_data}},
                {"type": "text", "text": prompt},
            ],
        }],
    )
    return response.content[0].text


def call_vision_batch(image_data_list: list[tuple[bytes, str]], prompt: str, max_tokens: int = 1024) -> str:
    """Multi-image vision call. image_data_list is list of (bytes, mime_type) tuples. Returns response text."""
    client = get_client()
    content = []
    for image_bytes, mime_type in image_data_list:
        b64_data = base64.standard_b64encode(image_bytes).decode("ascii")
        content.append({"type": "image", "source": {"type": "base64", "media_type": mime_type, "data": b64_data}})
    content.append({"type": "text", "text": prompt})
    response = client.messages.create(
        model=MODEL,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": content}],
    )
    return response.content[0].text


def call_text(prompt: str, max_tokens: int = 512) -> str:
    """Text-only call (no images). Returns response text."""
    client = get_client()
    response = client.messages.create(
        model=MODEL,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text
