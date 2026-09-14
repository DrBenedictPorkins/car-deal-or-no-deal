"""FastAPI application.

Local-first: binds loopback, serves the built frontend from the same process, and does
no network I/O of its own unless an LLM provider is explicitly enabled.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import __version__, logging_config
from app.api import dealers, drafts, interactions, knowledge, offers, profile, system, views
from app.config import get_settings
from app.db import engine, session_scope
from app.models import Base
from app.services.states import ensure_states

log = logging.getLogger("dealbench")

FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging_config.configure()
    settings = get_settings()

    if settings.host not in ("127.0.0.1", "localhost", "::1") and not (
        settings.allow_non_loopback_bind and settings.api_token
    ):
        raise RuntimeError(
            "Refusing to bind a non-loopback address without "
            "DEALBENCH_ALLOW_NON_LOOPBACK_BIND=true and DEALBENCH_API_TOKEN set. "
            "This app has no authentication and holds your negotiation data."
        )

    # Alembic owns migrations; create_all is the first-run convenience so a fresh
    # checkout starts without a migration step. Both target the same metadata.
    Base.metadata.create_all(bind=engine)
    with session_scope() as db:
        ensure_states(db)

    log.info("dealbench %s ready — data in %s", __version__, settings.data_dir)
    if settings.llm_enabled:
        log.info("LLM egress ENABLED via provider %r", settings.llm_provider)
    else:
        log.info("LLM egress disabled — nothing leaves this machine")
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="dealbench",
        version=__version__,
        description="Local-first dealership negotiation state engine",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    for module in (profile, dealers, interactions, offers, knowledge, drafts, views, system):
        app.include_router(module.router)

    if FRONTEND_DIST.is_dir():
        app.mount(
            "/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets"
        )

        @app.get("/{full_path:path}", include_in_schema=False)
        def spa(full_path: str):  # noqa: ARG001 - catch-all for client-side routing
            return FileResponse(FRONTEND_DIST / "index.html")

    return app


app = create_app()
