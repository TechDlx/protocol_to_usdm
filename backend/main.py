"""FastAPI application entry point.

Run locally with:  uv run uvicorn backend.main:create_app --factory --reload

An app factory (rather than a module-level `app`) keeps imports side-effect free: nothing reads
.env or creates the studies folder until the server actually starts.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api import review, runs, studies
from backend.config import Settings, get_settings
from backend.logging_setup import configure_logging
from backend.pipeline.jobs import JobRunner, LlmFactory
from backend.pipeline.llm import AnthropicLlm
from backend.storage.studies import StudyStore

VITE_DEV_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]

log = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.secret_values())
    store = StudyStore(settings.resolved_studies_root(), settings.max_upload_mb * 1024 * 1024)
    api_key = settings.anthropic_api_key
    llm_factory: LlmFactory | None = (
        (lambda: AnthropicLlm(api_key.get_secret_value())) if api_key is not None else None
    )
    jobs = JobRunner(store, llm_factory=llm_factory)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        interrupted = store.mark_interrupted_runs()
        if interrupted:
            log.warning("marked interrupted runs as failed", extra={"count": interrupted})
        yield
        jobs.shutdown()

    app = FastAPI(title="Protocol to USDM", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.store = store
    app.state.jobs = jobs
    app.add_middleware(
        CORSMiddleware,
        allow_origins=VITE_DEV_ORIGINS,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(studies.router)
    app.include_router(runs.router)
    app.include_router(runs.agents_router)
    app.include_router(review.router)
    app.include_router(review.terminology_router)

    @app.get("/api/health")
    def health() -> dict[str, object]:
        return {
            "status": "ok",
            "anthropic_key_configured": settings.anthropic_api_key is not None,
            "cdisc_key_configured": settings.cdisc_api_key is not None,
        }

    return app
