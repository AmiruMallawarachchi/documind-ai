"""
models/document.py — Pydantic schemas for the ingestion and retrieval layer.

These are the API's contract. FastAPI validates inbound JSON against them and
serialises outbound responses through them, so a malformed client payload is
rejected with a 422 before it ever reaches the database layer.

WHY schemas live in their own module:
  - routers, services, and tests all import the same definitions, so there is
    exactly one source of truth for what a "document" looks like.
  - Keeping them out of routers prevents the common FastAPI anti-pattern where
    response shapes are implicit dicts that silently drift between endpoints.
"""

from enum import Enum

from pydantic import BaseModel, Field


class DocumentStatus(str, Enum):
    """Status of a file through the ingestion pipeline.

    Stored as a str Enum so it serialises to a readable JSON value while still
    being type-safe on the Python side.
    """

    uploading = "uploading"
    parsing = "parsing"
    indexed = "indexed"
    failed = "failed"


class ChunkRecord(BaseModel):
    """One indexed slice of text, as it is stored in Pinecone.

    chunk_text lives in the vector DB rather than Mongo on purpose: MongoDB
    Atlas M0 caps at 512 MB, so full document text must not be duplicated there.
    """

    chunk_id: str
    document_id: str
    owner_id: str
    chunk_index: int
    page_number: int
    chunk_text: str
    token_estimate: int = Field(
        default=0,
        description="Rough whitespace token count; used for chunking diagnostics only.",
    )


class DocumentResponse(BaseModel):
    """The metadata record returned to the client after an upload."""

    document_id: str
    owner_id: str
    filename: str
    content_type: str
    status: DocumentStatus
    page_count: int = 0
    chunk_count: int = 0
    size_bytes: int = 0
    uploaded_at: str
    error: str | None = None


class UploadAcceptedResponse(BaseModel):
    """Returned immediately by POST /api/upload.

    Ingestion runs in a background task, so the client gets the record back
    with status='uploading' and then polls the documents list for progress.
    This keeps a slow embedding step from holding an HTTP request open.
    """

    documents: list[DocumentResponse]
    detail: str


class Citation(BaseModel):
    """A source reference attached to an answer.

    Rendered in the UI as [3] -> filename, page. Kept as an explicit model so
    the frontend contract is documented by the OpenAPI schema rather than by a
    comment.
    """

    index: int = Field(..., description="1-based position used in the inline marker.")
    document_id: str
    filename: str
    chunk_id: str
    page_number: int
    snippet: str = Field(..., description="Leading text of the source chunk.")


class RetrievedChunk(BaseModel):
    """A Pinecone match, joined with its metadata, ready for synthesis."""

    chunk_id: str
    document_id: str
    filename: str
    page_number: int
    chunk_text: str
    score: float
