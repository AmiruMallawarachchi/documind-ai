"""
routers/health.py — Two probes with two different jobs.

/api/health is liveness. It answers in microseconds and it never depends on anything, because Render
kills and restarts a container whose health check fails. If this route consulted Atlas, a five-second
database blip would look like a dead process and turn one slow query into a restart loop — on the free
tier's 750 instance-hours a month, that is an outage you cause yourself.

/api/health/deep is readiness, and it is for the developer rather than the platform: the first question
after setting credentials in a dashboard is "did they actually work?", and answering it with an upload
attempt is slow and misleading. It reports each dependency's own status and still returns 200, because
a degraded dependency is information, not a reason to fail a request. It is never wired to an automated
restart policy.
"""

import asyncio
import logging

from fastapi import APIRouter

from app.core.config import settings
from app.services import metadata_store

logger = logging.getLogger(__name__)

router = APIRouter()

# A dependency probe must not be able to hold the request open forever; Mongo's own driver timeout is
# bounded at 5 s, and Pinecone's client is slower, so the whole fan-out gets a shared ceiling.
_DEEP_TIMEOUT_SECONDS = 8.0


@router.get("/health", tags=["health"])
async def health_check() -> dict:
    """Confirm the process is up. Deliberately dependency-free; see the module docstring."""
    return {"status": "ok", "version": settings.app_version}


@router.get("/health/deep", tags=["health"])
async def deep_health_check() -> dict:
    """Report whether each backing service answers, without changing the liveness contract."""
    mongodb, pinecone = await asyncio.gather(
        _probe("mongodb", metadata_store.ping),
        _probe("pinecone", _pinecone_reachable),
    )

    return {
        "status": "ok",
        "version": settings.app_version,
        "environment": settings.environment,
        "index": settings.pinecone_index_name,
        "services": {"mongodb": mongodb, "pinecone": pinecone},
    }


async def _probe(name: str, check) -> str:
    """Run one blocking probe off the event loop, with a hard ceiling and a short vocabulary."""
    try:
        reachable = await asyncio.wait_for(asyncio.to_thread(check), timeout=_DEEP_TIMEOUT_SECONDS)
        return "up" if reachable else "down"
    except TimeoutError:
        logger.warning("deep health probe timed out service=%s", name)
        return "timeout"
    except Exception as exc:
        # The exception class only: a driver message can carry the connection string, and the
        # connection string carries the password.
        logger.warning("deep health probe failed service=%s error=%s", name, type(exc).__name__)
        return "down"


def _pinecone_reachable() -> bool:
    from app.services.vector_store import index_statistics

    # index_statistics degrades to an error key rather than raising, which is the only reason it is
    # usable as a probe at all.
    return index_statistics().get("total_vector_count") is not None
