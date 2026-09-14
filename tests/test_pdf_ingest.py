"""PDF на входе: текстовый слой, скан и совсем нечитаемый файл."""

from __future__ import annotations

import pytest

from src.ingest.pdf import MAX_OCR_PAGES, pdf_to_images, pdf_to_text

fitz = pytest.importorskip("fitz", reason="PyMuPDF — опциональный extra [pdf]")


def _pdf(pages: int = 1, *, text: str | None = "Глюкоза 5.4 ммоль/л") -> bytes:
    doc = fitz.open()
    for n in range(pages):
        page = doc.new_page()
        if text:
            page.insert_text((72, 144), f"{text} #{n}")
    return doc.tobytes()


def test_text_layer_is_read_locally():
    assert "5.4" in pdf_to_text(_pdf())


def test_a_scan_has_no_text_layer():
    assert pdf_to_text(_pdf(text=None)) == ""


def test_pages_render_to_png_for_the_vision_path():
    pages = pdf_to_images(_pdf(text=None))
    assert len(pages) == 1
    assert pages[0].startswith(b"\x89PNG\r\n\x1a\n")


def test_only_the_first_pages_are_rendered():
    """Один вызов модели на страницу — дальше первых страниц не идём."""
    assert len(pdf_to_images(_pdf(pages=MAX_OCR_PAGES + 4))) == MAX_OCR_PAGES


def test_garbage_yields_nothing_instead_of_raising():
    assert pdf_to_text(b"not a pdf at all") == ""
    assert pdf_to_images(b"not a pdf at all") == []
