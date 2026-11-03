"""
services/text_extractor.py — Turn an uploaded file into page-tagged text segments.

Every downstream step (chunking, embedding, citation) needs to know *where* a piece of
text came from, so extraction returns segments that each carry a page number rather than
one flat string. A citation saying "page 7" is only honest if page 7 survived the pipeline.

Deliberate scope limit: no OCR. A scanned/image-only PDF yields little extractable text,
and we surface that as a clean, actionable error instead of silently indexing empty
chunks — a silent empty index shows up weeks later as "the RAG is bad" rather than as a
parse failure.
"""

from dataclasses import dataclass
import io
import logging
import re

from app.core.config import settings

logger = logging.getLogger(__name__)


class UnsupportedFileType(ValueError):
    """Extension is not on the allowlist. Maps to HTTP 415, never a 500."""


class ExtractionError(ValueError):
    """Permitted file type whose contents could not be read. Maps to HTTP 422."""


class EmptyDocumentError(ExtractionError):
    """No usable text — typically a scanned PDF with no embedded character layer."""


@dataclass(frozen=True)
class PageSegment:
    """One page's worth of extracted text, 1-indexed."""

    page_number: int
    text: str


def normalise_whitespace(text: str) -> str:
    """Collapse layout noise that PDF extraction drags in.

    WHY: PDF extraction preserves visual layout, so a justified paragraph arrives as many
    short ragged lines. Embedding models read meaning, not layout, and raw line breaks
    inside a sentence degrade chunk coherence and make stored citation text ugly. We join
    soft-wrapped lines and squash whitespace runs while keeping paragraph breaks intact.
    """
    no_crlf = text.replace("\r\n", "\n").replace("\r", "\n")
    # A single newline between two non-empty lines is a soft wrap, not a paragraph break.
    paragraphs = re.split(r"\n\s*\n", no_crlf)
    cleaned: list[str] = []
    for paragraph in paragraphs:
        lines = [line.strip() for line in paragraph.split("\n") if line.strip()]
        if not lines:
            continue
        joined = lines[0]
        for line in lines[1:]:
            if joined.endswith("-"):
                # Typographic hyphenation: the printer broke "soft-wrapped" across a line, so that
                # hyphen is not part of the word. A space join leaves "soft- wrapped", which tokenises
                # as two meaningless fragments and pollutes the embedding of every chunk holding it.
                joined = joined[:-1] + line
            else:
                joined = f"{joined} {line}"
        joined = re.sub(r"[ \t]{2,}", " ", joined).strip()
        if joined:
            cleaned.append(joined)
    return "\n\n".join(cleaned)


def _extract_pdf(data: bytes) -> list[PageSegment]:
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception as exc:  # pypdf raises many opaque types; the message is what matters
        raise ExtractionError(f"PDF could not be opened: {exc}") from exc

    if reader.is_encrypted:
        try:
            if not reader.decrypt(""):
                raise ExtractionError("PDF is password-protected; upload an unlocked copy.")
        except ExtractionError:
            raise
        except Exception as exc:
            raise ExtractionError(f"PDF decryption failed: {exc}") from exc

    segments: list[PageSegment] = []
    for index, page in enumerate(reader.pages, start=1):
        try:
            raw = page.extract_text() or ""
        except Exception as exc:
            # One corrupt page should not lose the other 199; log it and carry on.
            logger.warning("page %d extraction failed for PDF: %s", index, exc)
            raw = ""
        text = normalise_whitespace(raw)
        if text:
            segments.append(PageSegment(page_number=index, text=text))
    return segments


def _extract_docx(data: bytes) -> list[PageSegment]:
    """python-docx exposes paragraphs, not pages.

    DOCX has no reliable page model — pagination is a layout-time computation the library
    does not do — so all text becomes one segment labelled page 1. Cross-document retrieval
    still works because chunk_text and document identity, not page geometry, drive it.
    """
    import docx

    try:
        document = docx.Document(io.BytesIO(data))
    except Exception as exc:
        raise ExtractionError(f"DOCX could not be opened: {exc}") from exc

    parts: list[str] = []
    for paragraph in document.paragraphs:
        if paragraph.text.strip():
            parts.append(paragraph.text)
    for table in document.tables:
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
            if cells:
                parts.append(" | ".join(cells))

    text = normalise_whitespace("\n\n".join(parts))
    return [PageSegment(page_number=1, text=text)] if text else []


def _extract_txt(data: bytes) -> list[PageSegment]:
    # Legacy files often arrive Windows-1252; strict UTF-8 would reject them outright and
    # 'replace' keeps surrounding content useful even when one byte is invalid.
    text = normalise_whitespace(data.decode("utf-8", errors="replace"))
    return [PageSegment(page_number=1, text=text)] if text else []


def extract_segments(filename: str, data: bytes) -> list[PageSegment]:
    """Dispatch on extension and return page-tagged segments.

    The extension is checked against settings.allowed_extensions_set — the same allowlist the
    router enforces on size — so validation lives in one place and cannot drift.
    """
    lower = filename.lower()
    dot = lower.rfind(".")
    extension = lower[dot:] if dot != -1 else ""

    if extension not in settings.allowed_extensions_set:
        supported = ", ".join(sorted(settings.allowed_extensions_set))
        raise UnsupportedFileType(
            f"'{extension or lower}' is not supported. Allowed types: {supported}. "
            "Scanned/image-only PDFs are out of scope (no OCR in v1)."
        )

    if not data:
        raise ExtractionError(f"{filename} is empty.")
    if len(data) > settings.max_upload_bytes:
        raise ExtractionError(
            f"{filename} is {len(data):,} bytes, over the "
            f"{settings.max_upload_bytes:,} byte cap."
        )

    if extension == ".pdf":
        segments = _extract_pdf(data)
    elif extension == ".docx":
        segments = _extract_docx(data)
    elif extension == ".txt":
        segments = _extract_txt(data)
    else:
        raise UnsupportedFileType(f"'{extension}' reached the extractor but is not handled.")

    if not segments:
        raise EmptyDocumentError(
            f"No selectable text found in {filename}. "
            "Scanned or image-only PDFs need OCR, which is out of scope for v1."
        )
    return segments
