"""Render the romanesco mark into the PWA icons (web/app/icons/*.png).

Pure standard library: the buds are drawn analytically into a supersampled
alpha buffer and written out as PNG by hand, so the repo needs no image
dependency. Geometry matches web/js/romanesco.js — one logarithmic spiral,
bud k at angle k*GA and distance G**k, every bud a head of its own.

Usage: python scripts/gen_app_icons.py
"""

from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

GA = math.pi * (3 - math.sqrt(5))
G = 1.0405
FLORET = 0.315
RIM = 1 + FLORET
BUDS = 55          # buds per head — fewer than on screen, so the mark reads small
SUB_BUDS = 21

OUT_DIR = Path(__file__).resolve().parent.parent / "web" / "app" / "icons"

BG = (0xF3, 0xF8, 0xF7)
INK = (0x0C, 0x8F, 0x86)
SS = 3  # supersampling factor


def _head(cx: float, cy: float, unit: float, rot: float, depth: int, open_at: float,
          out: list[tuple[float, float, float, float, bool]]) -> None:
    """Collect (x, y, radius, alpha, filled) for one head and its buds."""
    count = BUDS if depth == 0 else SUB_BUDS
    last = 0.0
    for k in range(0, -count - 1, -1):
        r = unit * G**k
        rad = r * FLORET
        if rad < 0.6:
            break
        ang = k * GA + rot
        x, y = cx + r * math.cos(ang), cy + r * math.sin(ang)
        out.append((x, y, rad, 1.0, False))
        last = r
        if rad >= open_at and depth < 1:
            _head(x, y, rad / RIM, ang + k * GA, depth + 1, open_at, out)
    if last:                        # the tip: the buds run out before the spiral does
        out.append((cx, cy, last * RIM * 0.7, 1.0, True))


def _draw(size: int, head_fraction: float) -> bytes:
    """Draw the mark at `size` px and return the RGBA rows as raw bytes."""
    big = size * SS
    cover = [0.0] * (big * big)

    buds: list[tuple[float, float, float, float, bool]] = []
    unit = big * head_fraction / RIM
    _head(big / 2, big / 2, unit, 0.0, 0, big / 22, buds)

    for x, y, rad, alpha, filled in buds:
        half = max(1.15 * SS, rad * 0.07) / 2
        lo_y, hi_y = int(y - rad - half - 1), int(y + rad + half + 2)
        lo_x, hi_x = int(x - rad - half - 1), int(x + rad + half + 2)
        for py in range(max(0, lo_y), min(big, hi_y)):
            dy = py + 0.5 - y
            row = py * big
            for px in range(max(0, lo_x), min(big, hi_x)):
                dx = px + 0.5 - x
                dist = math.hypot(dx, dy)
                d = dist - rad if filled else abs(dist - rad)
                if d <= half:
                    i = row + px
                    if cover[i] < alpha:
                        cover[i] = alpha

    # box-downsample the supersampled coverage into RGBA rows
    rows = bytearray()
    inv = 1.0 / (SS * SS)
    for y in range(size):
        rows.append(0)  # PNG filter: none
        base = y * SS
        for x in range(size):
            acc = 0.0
            for sy in range(SS):
                off = (base + sy) * big + x * SS
                for sx in range(SS):
                    acc += cover[off + sx]
            a = acc * inv
            rows.extend(bytes(round(BG[c] + (INK[c] - BG[c]) * a) for c in range(3)))
            rows.append(255)
    return bytes(rows)


def _png(size: int, rows: bytes) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(rows, 9))
            + chunk(b"IEND", b""))


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    # "any" icons fill the square; the maskable one keeps its head inside the
    # 80 % safe circle Android crops to.
    for name, size, fraction in (
        ("icon-192.png", 192, 0.90),
        ("icon-512.png", 512, 0.90),
        ("icon-maskable-512.png", 512, 0.62),
    ):
        path = OUT_DIR / name
        path.write_bytes(_png(size, _draw(size, fraction / 2)))
        print(f"{path} — {path.stat().st_size} bytes")


if __name__ == "__main__":
    main()
