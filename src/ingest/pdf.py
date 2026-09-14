"""PDF handling for lab reports: text layer first, rendered pages second.

PyMuPDF is an optional extra (`pip install .[pdf]`): a text-layer PDF is parsed
locally and never reaches the model as an image, which is both cheaper and more
accurate. A scan has no text layer — its pages are rendered to PNG here and go
through the same vision path as a photo of the page, so the user is not asked
to re-photograph a document they already sent.
"""

from __future__ import annotations

from src.logging_setup import get_logger

log = get_logger("ingest.pdf")

MAX_PAGES = 10
# Pages rendered for OCR. Every page is a separate image in one model call, so
# this is a cost cap, not a parser limit: lab reports put the numbers on the
# first pages, the rest is reference ranges and letterhead.
MAX_OCR_PAGES = 3
# 200 dpi — small print in a lab table stays readable, a page stays under ~1 MB.
OCR_DPI = 200


def pdf_to_text(data: bytes, *, max_pages: int = MAX_PAGES) -> str:
    """Extracted text, or "" when there is no text layer / no parser installed."""
    try:
        import fitz  # PyMuPDF
    except ImportError:
        log.info("pymupdf not installed; falling back to vision OCR")
        return ""
    try:
        with fitz.open(stream=data, filetype="pdf") as document:
            chunks = [page.get_text() for page in list(document)[:max_pages]]
    except Exception as exc:
        log.warning("pdf parse failed: %s", exc)
        return ""
    text = "\n".join(chunk.strip() for chunk in chunks if chunk and chunk.strip())
    return text.strip()


def pdf_to_images(
    data: bytes, *, max_pages: int = MAX_OCR_PAGES, dpi: int = OCR_DPI
) -> list[bytes]:
    """Pages as PNG bytes, oldest-first; empty when nothing can be rendered."""
    try:
        import fitz  # PyMuPDF
    except ImportError:
        log.info("pymupdf not installed; cannot render pdf pages")
        return []
    out: list[bytes] = []
    try:
        with fitz.open(stream=data, filetype="pdf") as document:
            for page in list(document)[:max_pages]:
                out.append(page.get_pixmap(dpi=dpi).tobytes("png"))
    except Exception as exc:
        log.warning("pdf render failed: %s", exc)
        return []
    return out


__all__ = ["MAX_OCR_PAGES", "MAX_PAGES", "OCR_DPI", "pdf_to_images", "pdf_to_text"]
