"""
routers/health.py — The endpoint Render polls to decide whether this container is alive.

Kept dependency-free on purpose: a restart on an upstream blip takes the whole service down for every
visitor, so a 500 here caused by Atlas or Pinecone would be worse than the outage it reports. The probe
that does check them lives on its own path, added with the logging stage.
"""

from fastapi import APIRouter

from app.core.config import settings

router = APIRouter()


@router.get("/health", tags=["health"])
async def health_check() -> dict:
    """Confirm the process is up and answer with the version, for the deploy log and for Render."""
    return {"status": "ok", "version": settings.app_version}
