from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import DbSession
from app.config import get_settings
from app.llm import available_providers
from app.services import contradictions, notifications, signals, state_engine

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/health")
def health():
    settings = get_settings()
    return {
        "status": "ok",
        # Explicit about egress: the UI shows this so "is anything leaving my machine?"
        # is answerable at a glance.
        "llm_enabled": settings.llm_enabled,
        "llm_provider": settings.llm_provider if settings.llm_enabled else "null",
        "llm_providers_available": available_providers(),
        "data_dir": str(settings.data_dir),
    }


@router.post("/refresh")
def refresh(db: DbSession):
    """Re-run every deterministic pass: signals, states, contradictions, notifications.

    Cheap and idempotent — every writer in the chain dedupes — so the UI can call it
    after any change without worrying about ordering.
    """
    signal_count = signals.refresh_signals(db)
    transitions = state_engine.refresh_all(db)
    found = contradictions.detect_all(db)
    notes = notifications.refresh(db)

    return {
        "signals_added": signal_count,
        "state_transitions": len(transitions),
        "contradictions": len(found),
        "notifications": len(notes),
    }
