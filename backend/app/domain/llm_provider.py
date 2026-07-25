"""
LLM Provider abstraction (SRS §8.3).

Defines the `LLMProvider` protocol that concrete implementations
(OllamaProvider, OpenAICompatibleProvider) must satisfy. The domain
layer only knows this protocol — it never imports provider-specific
HTTP clients or SDKs.

Per SRS §8.4: LLM output is never auto-executed. The AI emits
PlannedAction objects with status=pending_review; a human must approve
via the API before anything is executed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(slots=True)
class LLMMessage:
    """A single message in an LLM conversation."""

    role: str  # "system", "user", "assistant"
    content: str


@dataclass(slots=True)
class LLMResponse:
    """Structured response from an LLM provider."""

    content: str
    model: str
    usage: dict[str, int] = field(default_factory=dict)
    finish_reason: str | None = None


class LLMProvider(Protocol):
    """
    Protocol for LLM backends (SRS §8.3).

    Concrete implementations: `OllamaProvider` (local, default for
    self-host/air-gapped labs), `OpenAICompatibleProvider` (for hosted
    endpoints). Swappable per Organization via config.
    """

    async def complete(
        self,
        messages: list[LLMMessage],
        response_schema: dict[str, object] | None = None,
    ) -> LLMResponse: ...
