"""
routers/documents.py — Upload, list, inspect, and delete documents.

These routes are thin by design: validate the request, hand off to a service, shape the response.
Business rules (how a document is parsed, what status transitions are legal) live in services/, so a
second caller — a CLI script, a test, a future scheduled re-index — can reuse the pipeline without
importing anything web-specific.

Upload returns 202 Accepted rather than 200 with a finished document. Indexing takes seconds to
minutes, and an endpoint that blocks until it is done is an endpoint that times out behind a proxy.
"""

from __future__ import annotations

import logging
import os

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Response, UploadFile, status

from app.core.config import settings
from app.core.deps import current_owner_id, upload_rate_limit
from app.models.document import (
    DocumentResponse,
    DocumentStatus,
    UploadAcceptedResponse,
)
from app.services import ingestion, metadata_store

logger = logging.getLogger(__name__)

router = APIRouter(tags=["documents"])


def _validate_upload(file: UploadFile) -> None:
    """Reject anything that should never reach storage or the parser.

    An allowlist, not a denylist: 'not .exe' is a list you will lose. Checking the extension before
    reading the body means a hostile or accidental 500 MB upload is refused on the metadata alone
    instead of after being buffered.
    """
    if not file.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Upload must include a filename.",
        )

    extension = os.path.splitext(file.filename)[1].lower()
    if extension not in settings.allowed_extensions_set:
        supported = ", ".join(sorted(settings.allowed_extensions_set))
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"'{extension or 'unknown'}' is not supported. Allowed types: {supported}.",
        )


def _read_capped(file: UploadFile) -> bytes:
    """Read at most max_upload_bytes + 1 so an oversized body cannot define the buffer size.

    WHY: file.read() with no bound lets a client allocate memory equal to its upload. Reading one
    byte past the cap proves the limit was exceeded without ever materialising a giant file — on
    Render's 512 MB free tier an unbounded read is a container-killer, not a 413.
    """
    payload = file.file.read(settings.max_upload_bytes + 1)
    if len(payload) > settings.max_upload_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=(
                f"File exceeds the {settings.max_upload_bytes // (1024 * 1024)} MB limit "
                f"({len(payload):,}+ bytes)."
            ),
        )
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{file.filename} is empty.",
        )
    return payload


@router.post("/upload", status_code=status.HTTP_202_ACCEPTED, response_model=UploadAcceptedResponse)
async def upload_documents(
    background: BackgroundTasks,
    files: list[UploadFile] = File(..., description="One or more PDF/DOCX/TXT files."),
    owner_id: str = Depends(current_owner_id),
    _: None = Depends(upload_rate_limit),
) -> UploadAcceptedResponse:
    """Queue one or more files for parsing, chunking, embedding, and indexing.

    The response describes the documents as they were accepted (status='uploading'); the client polls
    GET /api/documents for progress. Returning the ids immediately is what lets the UI show a per-file
    progress row rather than one blocking spinner over an unknown wait.

    The limit runs before a single byte is read: an upload costs an embedding pass over the whole file, so
    it is the most expensive thing a caller can ask this server to do.
    """
    if not files:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No files provided.")

    accepted: list[DocumentResponse] = []

    for file in files:
        _validate_upload(file)
        payload = _read_capped(file)
        document_id = ingestion.new_document_id()

        record = metadata_store.create_document_record(
            document_id=document_id,
            owner_id=owner_id,
            filename=file.filename or "unnamed",
            content_type=file.content_type or "application/octet-stream",
            size_bytes=len(payload),
        )

        # add_task after the row exists: if the process died between insert and scheduling, the
        # document is at worst stuck at 'uploading' rather than referenced by nothing at all.
        background.add_task(
            ingestion.run_ingestion,
            document_id=document_id,
            owner_id=owner_id,
            filename=file.filename or "unnamed",
            data=payload,
        )
        accepted.append(metadata_store.to_response(record))

    if not accepted:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No valid files uploaded.")

    return UploadAcceptedResponse(
        documents=accepted,
        detail=(
            f"Accepted {len(accepted)} file(s); indexing runs in the background. "
            "Poll GET /api/documents for status."
        ),
    )


@router.get("/documents", response_model=list[DocumentResponse])
async def list_documents(
    owner_id: str = Depends(current_owner_id),
    limit: int = 100,
) -> list[DocumentResponse]:
    """The user's documents, newest first, including ones still indexing."""
    limit = max(1, min(limit, 500))
    records = metadata_store.list_documents(owner_id, limit=limit)
    return [metadata_store.to_response(record) for record in records]


@router.get("/documents/{document_id}", response_model=DocumentResponse)
async def get_document(
    document_id: str,
    owner_id: str = Depends(current_owner_id),
) -> DocumentResponse:
    """Single document — the status-polling endpoint the upload flow points at."""
    record = metadata_store.get_document(document_id, owner_id)
    if record is None:
        # 404 rather than 403: distinguishing "does not exist" from "exists but is not yours" tells an
        # attacker which ids are real, so both cases answer identically.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found.",
        )
    return metadata_store.to_response(record)


@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: str,
    owner_id: str = Depends(current_owner_id),
) -> Response:
    """Delete vectors and metadata together, or report that the sweep did not complete.

    Returns Response, not None. FastAPI derives response_model from the return annotation and only
    skips that when the annotation is a Response subclass: annotating `-> None` makes NoneType the
    response model, and the route then asserts "status 204 must not have a response body" at
    import time — the app would refuse to boot on an endpoint that is perfectly correct.
    """
    removed = ingestion.delete_document_completely(document_id=document_id, owner_id=owner_id)
    if not removed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found, or it is still being indexed. Try again shortly.",
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/documents/status/summary")
async def document_status_summary(
    owner_id: str = Depends(current_owner_id),
) -> dict[str, int]:
    """Counts per status, cheap enough for the UI to poll while several uploads are in flight."""
    records = metadata_store.list_documents(owner_id, limit=500)
    counts = {state.value: 0 for state in DocumentStatus}
    for record in records:
        state = record.get("status", DocumentStatus.uploading.value)
        counts[state] = counts.get(state, 0) + 1
    return counts
