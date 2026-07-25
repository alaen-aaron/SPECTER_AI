"""
OpenAI-compatible LLM provider (SRS §8.3).

For hosted Qwen/Llama/Gemma-serving endpoints or actual OpenAI-compatible
APIs. Swappable per Organization via config.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger
from app.domain.llm_provider import LLMMessage, LLMResponse

logger = get_logger(__name__)


class OpenAICompatibleProvider:
    """
    OpenAI-compatible chat completions adapter.

    Uses the `/v1/chat/completions` endpoint which most OpenAI-compatible
    providers expose.
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
        }

        if response_schema is not None:
            payload["response_format"] = {"type": "json_object"}

        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(
                    f"{self._base_url}/v1/chat/completions",
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            logger.error("openai_compatible_request_failed", error=str(exc))
            raise

        choice = data.get("choices", [{}])[0]
        content = choice.get("message", {}).get("content", "")
        usage_data = data.get("usage", {})

        return LLMResponse(
            content=content,
            model=self._model,
            usage={
                "prompt_tokens": usage_data.get("prompt_tokens", 0),
                "completion_tokens": usage_data.get("completion_tokens", 0),
            },
            finish_reason=choice.get("finish_reason", "stop"),
        )
