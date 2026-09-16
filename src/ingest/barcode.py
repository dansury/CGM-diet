"""Barcodes read locally, before the photo costs a model call.

A pack photographed from the front usually carries its EAN, and an EAN is an
exact key into an open food database — no recognition, no guessing at the small
print. `pyzbar` (a binding to the system `zbar`) is an optional extra
(`pip install .[barcode]`): without it this module reports "no barcodes" and
the label path works exactly as before.

Only product symbologies are asked for. Left to itself, zbar happily reads the
QR code of a loyalty programme printed next to the ingredients, and that is not
a product id.
"""

from __future__ import annotations

from src.logging_setup import get_logger

log = get_logger("ingest.barcode")

#: EAN-13/8 and UPC-A/E — what a grocery pack carries. GS1 DataBar and the
#: QR codes next to it are not product ids and are deliberately not read.
SYMBOLOGIES = ("EAN13", "EAN8", "UPCA", "UPCE")


def read_barcodes(data: bytes) -> list[str]:
    """Digit strings found on the image, most likely first; [] when none."""
    decode, symbols = _zbar()
    if decode is None:
        return []
    try:
        from PIL import Image
    except ImportError:
        log.info("pillow not installed; barcode reading disabled")
        return []
    import io

    try:
        with Image.open(io.BytesIO(data)) as image:
            found = decode(image.convert("L"), symbols=symbols)
    except Exception as exc:
        log.warning("barcode decode failed: %s", exc)
        return []
    out: list[str] = []
    for item in found:
        text = item.data.decode("ascii", errors="ignore").strip()
        if text.isdigit() and text not in out and is_valid(text):
            out.append(text)
    return out


def is_valid(code: str) -> bool:
    """GS1 check digit. A misread digit is worse than no barcode at all:
    it silently names somebody else's product."""
    if not code.isdigit() or len(code) not in (8, 12, 13, 14):
        return False
    digits = [int(c) for c in code]
    check = digits.pop()
    total = 0
    # Weights alternate 3/1 from the right, whatever the length.
    for position, digit in enumerate(reversed(digits)):
        total += digit * (3 if position % 2 == 0 else 1)
    return (10 - total % 10) % 10 == check


def normalize(code: str) -> str:
    """UPC-A to the EAN-13 spelling Open Food Facts stores."""
    return "0" + code if len(code) == 12 else code


def _zbar():
    """`(decode, symbols)` or `(None, None)` when the extra is not installed."""
    try:
        from pyzbar import pyzbar
    except ImportError:
        log.info("pyzbar not installed; barcode reading disabled")
        return None, None
    except Exception as exc:  # the binding raises when libzbar itself is absent
        log.info("pyzbar unusable (%s); barcode reading disabled", exc)
        return None, None
    symbols = [
        getattr(pyzbar.ZBarSymbol, name)
        for name in SYMBOLOGIES
        if hasattr(pyzbar.ZBarSymbol, name)
    ]
    return pyzbar.decode, symbols


__all__ = ["SYMBOLOGIES", "is_valid", "normalize", "read_barcodes"]
