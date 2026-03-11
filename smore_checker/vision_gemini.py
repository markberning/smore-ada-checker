"""Thin provider module for the Google Gemini API (google-genai SDK).

Raw API calls only - no caching, batching logic, logging, or sleeping.
The router handles all of that.
"""

import os
from google import genai
from google.genai import types

PROVIDER_NAME = "Gemini Flash"
MODEL = "gemini-2.5-flash"
RATE_LIMIT_DELAY = 2  # seconds between calls for free tier

_client = None


def get_client() -> genai.Client:
    """Lazy-init and return the Gemini client."""
    global _client
    if _client is None:
        _client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])
    return _client


def call_vision(image_bytes: bytes, mime_type: str, prompt: str, max_tokens: int = 1024) -> str:
    """Single-image vision call. Returns response text."""
    client = get_client()
    response = client.models.generate_content(
        model=MODEL,
        contents=[
            types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
            prompt,
        ],
        config=types.GenerateContentConfig(max_output_tokens=max_tokens),
    )
    return response.text


def call_vision_batch(
    image_data_list: list[tuple[bytes, str]], prompt: str, max_tokens: int = 1024
) -> str:
    """Multi-image vision call. image_data_list is list of (bytes, mime_type) tuples."""
    client = get_client()
    contents = [
        types.Part.from_bytes(data=img_bytes, mime_type=mime)
        for img_bytes, mime in image_data_list
    ]
    contents.append(prompt)
    response = client.models.generate_content(
        model=MODEL,
        contents=contents,
        config=types.GenerateContentConfig(max_output_tokens=max_tokens),
    )
    return response.text


def call_text(prompt: str, max_tokens: int = 512) -> str:
    """Text-only call (no images)."""
    client = get_client()
    response = client.models.generate_content(
        model=MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(max_output_tokens=max_tokens),
    )
    return response.text
