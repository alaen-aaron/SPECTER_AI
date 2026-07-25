"""
Ollama LLM provider (SRS §8.3).

Default provider for self-hosted / air-gapped labs. Talks to the
Ollama HTTP API at `LLM_BASE_URL`.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger
from app.domain.llm_provider import LLMMessage, LLMResponse

logger = get_logger(__name__)


class OllamaProvider:
    """
    Ollama HTTP API adapter.

    Uses httpx for async HTTP; the Ollama endpoint is
    `POST /api/chat` (or `/api/generate` for simpler prompts).
    """

    def __init__(self, base_url: str | None = None, model: str | None = None) -> None:
        settings = get_settings()
        self._base_url = (base_url or settings.LLM_BASE_URL).rstrip("/")
        self._model = model or settings.LLM_MODEL

    async def complete(
        self,
        messages: list[LLMMessage],
        response_schema: dict[str, object] | None = None,
    ) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": False,
        }

        if response_schema is not None:
            payload["format"] = "json"

        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(f"{self._base_url}/api/chat", json=payload)
                resp.raise_for_status()
                data = resp.json()
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            logger.error("ollama_request_failed", error=str(exc))
            raise

        content = data.get("message", {}).get("content", "")
        return LLMResponse(
            content=content,
            model=self._model,
            usage={
                "prompt_tokens": data.get("prompt_eval_count", 0),
                "completion_tokens": data.get("eval_count", 0),
            },
            finish_reason=data.get("done_reason", "stop"),
        )
