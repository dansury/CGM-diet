"""PDF text extraction for lab reports.

PyMuPDF is an optional extra (`pip install .[pdf]`): a text-layer PDF is parsed
locally and never reaches the model as an image, which is both cheaper and more
accurate. Scanned PDFs have no text layer — the caller then asks for a photo.
"""

from __future__ import annotations

from src.logging_setup import get_logger

log = get_logger("ingest.pdf")

MAX_PAGES = 10


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


__all__ = ["MAX_PAGES", "pdf_to_text"]
