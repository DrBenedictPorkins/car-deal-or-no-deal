"""Provider selection.

Chosen by configuration. Adding Anthropic, OpenAI or a local model is a new module
registered here — no domain code changes, because no domain code knows a vendor exists.
"""

from __future__ import annotations

from collections.abc import Callable

from app.config import get_settings
from app.llm.base import LLMProvider
from app.llm.null_provider import NullProvider

_FACTORIES: dict[str, Callable[[], LLMProvider]] = {
    "null": NullProvider,
}


def register(name: str, factory: Callable[[], LLMProvider]) -> None:
    _FACTORIES[name] = factory


def available_providers() -> list[str]:
    return sorted(_FACTORIES)


def get_provider(name: str | None = None) -> LLMProvider:
    settings = get_settings()
    # Egress is opt-in: with llm_enabled off, nothing but the null provider is reachable
    # regardless of what is configured.
    if not settings.llm_enabled:
        return NullProvider()
    key = (name or settings.llm_provider or "null").lower()
    factory = _FACTORIES.get(key)
    if factory is None:
        raise ValueError(
            f"unknown LLM provider {key!r}; registered: {', '.join(available_providers())}"
        )
    return factory()
