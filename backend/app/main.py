"""
main.py — FastAPI application factory for DocuMind AI.

Kept deliberately thin: create the app, wire middleware, register routers, own startup/shutdown.
Every new feature adds a router module under app/routers/ plus one include_router() line here. That
is what prevents the "everything lives in main.py" anti-pattern that makes a growing API impossible
to navigate or test.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Importing settings here validates every required env var before anything else runs, so a missing
# key aborts at boot with a clear message instead of failing mid-request deep inside a library stack.
from app.core.config import settings
from app.core.logging import AccessLogMiddleware, configure_logging
from app.routers import auth, chat, documents, health


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown hooks.

    The embedding model loads on startup rather than lazily on first upload: it costs several seconds
    and ~90 MB, and paying that inside a user's request is exactly how a free-tier instance behind a
    60-second proxy timeout produces its first mysterious 504.
    """
    configure_logging(production=settings.is_production)
    logger = logging.getLogger("documind.startup")
    logger.info(
        "documind-ai %s starting (env=%s)", settings.app_version, settings.environment
    )

    if not settings.is_production:
        # Surface a misconfigured Atlas now, instead of on the first real upload.
        try:
            from app.services import metadata_store

            logger.info("mongo reachable: %s", metadata_store.ping())
        except Exception as exc:
            logger.warning("mongo startup probe failed: %s", exc)

    try:
        from app.services.embedding_service import get_model

        get_model()
    except Exception as exc:
        # Deliberately not fatal: /api/health must still answer so Render keeps the instance up and
        # the deploy log states the real reason uploads are failing.
        logger.error("embedding model failed to load: %s", exc)

    yield

    logger.info("documind-ai shutting down")


app = FastAPI(
    title="DocuMind AI",
    description="Multi-agent document Q&A — LangGraph + Groq + Pinecone + MongoDB",
    version=settings.app_version,
    # Interactive docs stay on: for a portfolio project they are part of the demo.
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)

# ── Request context ───────────────────────────────────────────────────────────
# Added before CORSMiddleware so that CORS ends up the outer layer: a preflight response still carries
# X-Request-ID, and the access log measures the real handler rather than the options round trip.
app.add_middleware(AccessLogMiddleware)

# ── CORS ──────────────────────────────────────────────────────────────────────
# allow_origins comes from the env var, never "*".
# WHY: a wildcard lets any website in the world call your Render URL and spend your Groq/Pinecone
# free-tier quota on a stranger's behalf. Locking to your own origins means only your frontend can
# trigger billable work.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    # Listed explicitly rather than "*": the method set is known, and naming it means a typo in the
    # frontend shows up as a clear CORS error instead of quietly working with an unintended verb.
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    # Authorization is the header real clients now send; keeping the list explicit still means a typo in
    # the frontend surfaces as a clear CORS error rather than a preflight that quietly passed.
    allow_headers=["Authorization", "Content-Type", "X-Demo-Key", "X-Request-ID"],
    expose_headers=["X-Request-ID", "Retry-After"],
)

# ── Routers ───────────────────────────────────────────────────────────────────
# All routes live under /api so they are clearly namespaced apart from any static assets later.
app.include_router(health.router, prefix="/api")
# Session first: it is the endpoint every other call depends on, and it is the only unauthenticated route.
app.include_router(auth.router, prefix="/api")
app.include_router(documents.router, prefix="/api")
app.include_router(chat.router, prefix="/api")
