"""LLM provider abstraction.

No domain service may import this package. Only ``app.enrichment`` talks to a model,
and every call is recorded as an ``LLMRun`` so "which model said this, when, from what
prompt" is answerable after the fact.

The contract is deliberately narrow: ``complete`` for prose and ``extract`` for
schema-constrained structured output. Extraction returns *line items and quotes* —
never totals, because totals are arithmetic and arithmetic is application code's job.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


@dataclass
class CompletionRequest:
    purpose: str
    system: str
    prompt: str
    max_tokens: int = 2048
    temperature: float = 0.2


@dataclass
class CompletionResult:
    text: str
    provider: str
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: int | None = None


@dataclass
class ExtractionRequest(Generic[T]):
    purpose: str
    system: str
    prompt: str
    schema: type[T]
    max_tokens: int = 4096
    temperature: float = 0.0


@dataclass
class ExtractionResult(Generic[T]):
    value: T | None
    provider: str
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: int | None = None
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.value is not None and not self.errors


class LLMProvider(ABC):
    """Implementations live beside this file. Business logic references none of them."""

    name: str = "abstract"

    @abstractmethod
    def complete(self, request: CompletionRequest) -> CompletionResult: ...

    @abstractmethod
    def extract(self, request: ExtractionRequest[T]) -> ExtractionResult[T]: ...

    def available(self) -> bool:
        return True
