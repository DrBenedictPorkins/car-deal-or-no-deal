"""A provider that performs no network I/O.

This is the Phase 1 default, and it is not a placeholder to be removed later: it keeps
the test suite offline and deterministic, and it lets the whole application run with no
API key and no data leaving the machine.
"""

from __future__ import annotations

from app.llm.base import (
    CompletionRequest,
    CompletionResult,
    ExtractionRequest,
    ExtractionResult,
    LLMProvider,
    T,
)


class NullProvider(LLMProvider):
    name = "null"

    def complete(self, request: CompletionRequest) -> CompletionResult:
        return CompletionResult(
            text=(
                "[no LLM configured] This installation is running with the null provider, "
                "so nothing was sent anywhere. Set DEALBENCH_LLM_PROVIDER and enable "
                "DEALBENCH_LLM_ENABLED to use a real model."
            ),
            provider=self.name,
            model=None,
        )

    def extract(self, request: ExtractionRequest[T]) -> ExtractionResult[T]:
        return ExtractionResult(
            value=None,
            provider=self.name,
            errors=["null provider performs no extraction"],
        )

    def available(self) -> bool:
        return True
