"""Thin wrapper around the Google Gemini API (google-genai SDK)."""
from __future__ import annotations

import json
import os
import re

DEFAULT_MODEL = "gemini-3.8-flash"
REQUEST_TIMEOUT_MS = 30_000


class GeminiError(Exception):
    """Raised for any Gemini failure. Messages never contain secrets."""


class GeminiService:
    def __init__(self, api_key: str | None = None, model: str | None = None):
        self._api_key = (api_key or os.environ.get("GEMINI_API_KEY", "")).strip()
        self.model = model or os.environ.get("GEMINI_MODEL", DEFAULT_MODEL)
        self._client = None

    @property
    def available(self) -> bool:
        return bool(self._api_key) and self._api_key != "your_api_key_here"

    def _get_client(self):
        if self._client is None:
            try:
                from google import genai
                from google.genai import types

                self._client = genai.Client(
                    api_key=self._api_key,
                    http_options=types.HttpOptions(timeout=REQUEST_TIMEOUT_MS),
                )
            except Exception as exc:  # noqa: BLE001
                raise GeminiError("Could not initialise the Gemini client.") from exc
        return self._client

    def generate_json(self, prompt: str, temperature: float = 0.2) -> dict:
        """Send a prompt and return the parsed JSON object from the reply."""
        if not self.available:
            raise GeminiError("GEMINI_API_KEY is not configured.")
        client = self._get_client()
        try:
            from google.genai import types

            response = client.models.generate_content(
                model=self.model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json", temperature=temperature
                ),
            )
            text = response.text or ""
        except Exception as exc:  # noqa: BLE001
            raise GeminiError(f"Gemini request failed ({type(exc).__name__}).") from exc
        return _parse_json(text)


def _parse_json(text: str) -> dict:
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        try:
            data = json.loads(text[start : end + 1]) if start != -1 and end > start else None
        except json.JSONDecodeError:
            data = None
    if not isinstance(data, dict):
        raise GeminiError("Gemini returned a response that was not valid JSON.")
    return data
