"""
LLM Provider implementations (SRS §8.3).

Concrete adapters for Ollama (local, default) and OpenAI-compatible
APIs. Both implement the `LLMProvider` protocol defined in
`domain/llm_provider.py`.
"""

from __future__ import annotations

from app.infrastructure.llm.ollama_provider import OllamaProvider
from app.infrastructure.llm.openai_compatible_provider import OpenAICompatibleProvider

__all__ = ["OllamaProvider", "OpenAICompatibleProvider"]
