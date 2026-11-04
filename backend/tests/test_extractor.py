"""
tests/test_extractor.py — File-type handling, page fidelity, and the failure messages users see.

The PDF fixture is generated rather than committed: a real PDF in the repo is unreadable in review,
and a hand-built one states exactly which feature it is exercising.
"""

from __future__ import annotations

import io

import pytest

from app.services.text_extractor import (
    EmptyDocumentError,
    ExtractionError,
    UnsupportedFileType,
    extract_segments,
    normalise_whitespace,
)


def build_pdf(pages: list[str]) -> bytes:
    """Minimal valid PDF with correct xref offsets, one text object per page.

    Offsets must be accurate or pypdf falls back to a repair path and the test would be measuring the
    repair rather than the extraction.
    """
    objects: list[bytes] = [b"<< /Type /Catalog /Pages 2 0 R >>"]
    kids = " ".join(f"{3 + index * 2} 0 R" for index in range(len(pages)))
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode())
    font_id = 3 + len(pages) * 2
    for index, text in enumerate(pages):
        page_id = 3 + index * 2
        content_id = page_id + 1
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {content_id} 0 R "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> >>".encode()
        )
        stream = f"BT /F1 11 Tf 72 720 Td ({text}) Tj ET".encode()
        objects.append(f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"

    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode() + b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF".encode()
    return bytes(out)


def build_docx(paragraphs: list[str], table: list[list[str]] | None = None) -> bytes:
    import docx

    document = docx.Document()
    for text in paragraphs:
        document.add_paragraph(text)
    if table:
        built = document.add_table(rows=len(table), cols=len(table[0]))
        for row_index, row in enumerate(table):
            for cell_index, value in enumerate(row):
                built.rows[row_index].cells[cell_index].text = value
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def test_pdf_page_numbers_survive_extraction():
    """The single most important extractor property: a citation can only be truthful if the page did."""
    segments = extract_segments(
        "loan.pdf",
        build_pdf(
            [
                "The borrower agrees to repay the loan within 36 months.",
                "Late payment triggers a default fee of 250 dollars.",
                "Governing law is the law of Sri Lanka.",
            ]
        ),
    )
    assert [segment.page_number for segment in segments] == [1, 2, 3]
    assert "default fee" in segments[1].text


def test_txt_and_docx_are_supported():
    assert extract_segments("notes.txt", b"Plain text notes about the deal.")[0].text.startswith("Plain text")
    docx_segments = extract_segments("contract.docx", build_docx(["Loan of 10000 dollars.", "Repay in 24 months."]))
    assert "10000" in docx_segments[0].text


def test_docx_tables_are_extracted():
    """Contract terms hide in tables; ignoring them would silently lose the most queryable content."""
    segments = extract_segments(
        "terms.docx", build_docx(["Summary."], table=[["Term", "Value"], ["Late fee", "250 USD"]])
    )
    assert "Late fee | 250 USD" in segments[0].text


def test_unsupported_extension_is_rejected_before_reading_content():
    with pytest.raises(UnsupportedFileType) as exc:
        extract_segments("payload.exe", b"MZ\x00\x00")
    assert "not supported" in str(exc.value)


def test_scanned_pdf_reports_the_real_problem():
    """A PDF with no character layer must not become an empty index.

    This is the failure mode users hit most on a no-OCR build, so the message names the cause and the
    remedy instead of surfacing a generic parse error.
    """
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    scratch = io.BytesIO()
    writer.write(scratch)

    with pytest.raises(EmptyDocumentError) as exc:
        extract_segments("scan.pdf", scratch.getvalue())
    assert "OCR" in str(exc.value) or "Scanned" in str(exc.value)


def test_empty_and_oversized_files_are_refused():
    with pytest.raises(ExtractionError):
        extract_segments("empty.txt", b"")
    with pytest.raises(ExtractionError):
        extract_segments("huge.txt", b"x" * 11 * 1024 * 1024)


def test_soft_wrapped_lines_are_rejoined():
    """PDF layout must not leak into the text that gets embedded and shown in a citation."""
    assert normalise_whitespace("The soft-\nwrapped text is fine.\n\nNew para.") == (
        "The softwrapped text is fine.\n\nNew para."
    )


def test_paragraph_breaks_are_preserved():
    """Collapsing everything into one run would destroy the structure the chunker splits on."""
    result = normalise_whitespace("First point.\n\nSecond point.")
    assert "\n\n" in result


def test_password_protected_pdf_is_reported_not_crashed():
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.encrypt("secret")
    scratch = io.BytesIO()
    writer.write(scratch)

    with pytest.raises(ExtractionError) as exc:
        extract_segments("locked.pdf", scratch.getvalue())
    assert "password" in str(exc.value).lower()
