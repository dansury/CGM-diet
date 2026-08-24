"""Yandex SpeechKit STT. See `spec/ingest.md` § Голос (SpeechKit).

SpeechKit REST limits: <1 MB and <30 s per request. Longer OGG/Opus voice
messages are split at page boundaries into ~25 s chunks, each patched into a
valid standalone OGG stream (seqno, BOS/EOS, zeroed CRC). Pure Python, no ffmpeg.
Ported from the GrowthProducer bot (`src/tools/speechkit.py`); transport httpx.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

import httpx

from src.logging_setup import get_logger

log = get_logger("ingest.speechkit")

STT_URL = "https://stt.api.cloud.yandex.net/speech/v1/stt:recognize"
MAX_BYTES = 950_000
MAX_DURATION_SEC = 25  # chunk target (API hard limit is 30)
OPUS_SAMPLE_RATE = 48000  # Telegram voice = OGG/Opus @48kHz


class SpeechKitAuthError(RuntimeError):
    """Bad or missing API key (HTTP 401/403)."""


class SpeechKitQuotaExceeded(RuntimeError):
    """Rate limit hit (HTTP 429); caller may retry with backoff."""


class UnsupportedFormat(ValueError):
    """Input is not OGG/Opus (or empty)."""


@dataclass(frozen=True, slots=True)
class SpeechResult:
    text: str
    duration_sec: float
    language: str
    segments: list[dict] = field(default_factory=list)


async def _recognize_chunk(
    chunk: bytes,
    *,
    api_key: str,
    folder_id: str | None,
    lang: str,
    client: httpx.AsyncClient,
) -> str:
    params: dict[str, str] = {
        "lang": lang,
        "format": "oggopus",
        "sampleRateHertz": str(OPUS_SAMPLE_RATE),
        "model": "general:rc",
        "profanityFilter": "false",
        "rawResults": "false",
    }
    if folder_id:
        params["folderId"] = folder_id
    headers = {"Authorization": f"Api-Key {api_key}"}
    resp = await client.post(STT_URL, params=params, headers=headers, content=chunk)
    if resp.status_code == 200:
        return str(resp.json().get("result", ""))
    if resp.status_code in (401, 403):
        # Body carries the real reason — surface it in the logs.
        log.error(
            "speechkit auth rejected: %s folder=%s %s",
            resp.status_code,
            folder_id,
            resp.text[:300],
        )
        raise SpeechKitAuthError(f"speechkit auth failed: {resp.status_code}")
    if resp.status_code == 429:
        raise SpeechKitQuotaExceeded("speechkit rate limit")
    log.error("speechkit error: %s %s", resp.status_code, resp.text[:300])
    return ""


# ── OGG parsing (verbatim port) ─────────────────────────────


def _parse_ogg_pages(data: bytes) -> list[dict]:
    pages = []
    pos = 0
    while pos < len(data):
        if data[pos : pos + 4] != b"OggS":
            break
        if pos + 27 > len(data):
            break
        header_type = data[pos + 5]
        granule = struct.unpack_from("<q", data, pos + 6)[0]
        serial = struct.unpack_from("<I", data, pos + 14)[0]
        seqno = struct.unpack_from("<I", data, pos + 18)[0]
        n_segments = data[pos + 26]
        if pos + 27 + n_segments > len(data):
            break
        seg_table = data[pos + 27 : pos + 27 + n_segments]
        body_size = sum(seg_table)
        page_size = 27 + n_segments + body_size
        if pos + page_size > len(data):
            pages.append(
                {
                    "offset": pos,
                    "size": len(data) - pos,
                    "raw": data[pos:],
                    "granule": granule,
                    "serial": serial,
                    "seqno": seqno,
                    "header_type": header_type,
                }
            )
            break
        pages.append(
            {
                "offset": pos,
                "size": page_size,
                "raw": data[pos : pos + page_size],
                "granule": granule,
                "serial": serial,
                "seqno": seqno,
                "header_type": header_type,
            }
        )
        pos += page_size
    return pages


def _patch_page(raw: bytes, seqno: int, bos: bool = False, eos: bool = False) -> bytes:
    page = bytearray(raw)
    ht = page[5] & 0x01  # keep continuation bit
    if bos:
        ht |= 0x02
    if eos:
        ht |= 0x04
    page[5] = ht
    struct.pack_into("<I", page, 18, seqno)
    struct.pack_into("<I", page, 22, 0)  # CRC = 0 (SpeechKit tolerates)
    return bytes(page)


def _chunk_ogg(data: bytes) -> list[bytes]:
    pages = _parse_ogg_pages(data)
    if len(pages) < 3:
        return [data]

    header_pages: list[dict] = []
    body_pages: list[dict] = []
    for p in pages:
        if p["header_type"] & 0x02:  # BOS
            header_pages.append(p)
        elif not body_pages and len(header_pages) < 2:
            header_pages.append(p)  # comment page
        else:
            body_pages.append(p)

    if not body_pages:
        return [data]

    first_granule = body_pages[0]["granule"]
    if first_granule < 0:
        first_granule = 0

    max_granules = MAX_DURATION_SEC * OPUS_SAMPLE_RATE

    h0 = _patch_page(header_pages[0]["raw"], 0, bos=True)
    h1 = _patch_page(header_pages[1]["raw"], 1) if len(header_pages) > 1 else b""
    header_blob = h0 + h1
    header_size = len(header_blob)

    chunks: list[bytes] = []
    current_pages: list[dict] = []
    current_size = header_size
    chunk_start_granule = first_granule

    for bp in body_pages:
        granule = bp["granule"] if bp["granule"] >= 0 else chunk_start_granule
        duration_granules = granule - chunk_start_granule
        new_size = current_size + len(bp["raw"])

        if current_pages and (duration_granules >= max_granules or new_size > MAX_BYTES):
            chunk = bytearray(header_blob)
            for idx, cp in enumerate(current_pages):
                chunk.extend(_patch_page(cp["raw"], idx + 2))
            chunks.append(bytes(chunk))
            current_pages = []
            current_size = header_size
            chunk_start_granule = granule

        current_pages.append(bp)
        current_size += len(bp["raw"])

    if current_pages:
        chunk = bytearray(header_blob)
        for idx, cp in enumerate(current_pages):
            is_last = idx == len(current_pages) - 1
            chunk.extend(_patch_page(cp["raw"], idx + 2, eos=is_last))
        chunks.append(bytes(chunk))

    return chunks


def _duration_sec(data: bytes) -> float:
    pages = _parse_ogg_pages(data)
    granules = [p["granule"] for p in pages if p["granule"] > 0]
    return max(granules) / OPUS_SAMPLE_RATE if granules else 0.0


# ── Public API ──────────────────────────────────────────────


async def recognize_voice(
    audio_bytes: bytes,
    *,
    api_key: str,
    folder_id: str | None = None,
    lang: str = "ru-RU",
    client: httpx.AsyncClient | None = None,
) -> SpeechResult:
    """Recognize an OGG/Opus voice message; auto-chunks >25 s / >950 KB."""
    if not audio_bytes:
        raise UnsupportedFormat("empty audio payload")
    if audio_bytes[:4] != b"OggS":
        raise UnsupportedFormat("expected OGG/Opus (Telegram voice)")

    duration = _duration_sec(audio_bytes)
    needs_split = len(audio_bytes) > MAX_BYTES or duration > MAX_DURATION_SEC
    chunks = _chunk_ogg(audio_bytes) if needs_split else [audio_bytes]
    if needs_split:
        log.info(
            "speechkit split: %d bytes -> %d chunks (%.0f s)",
            len(audio_bytes),
            len(chunks),
            duration,
        )

    own_client = client is None
    http = client or httpx.AsyncClient(timeout=60)
    results: list[str] = []
    try:
        for i, chunk in enumerate(chunks):
            text = await _recognize_chunk(
                chunk, api_key=api_key, folder_id=folder_id, lang=lang, client=http
            )
            if text:
                results.append(text)
            log.debug("speechkit chunk %d/%d: %d chars", i + 1, len(chunks), len(text))
    finally:
        if own_client:
            await http.aclose()

    return SpeechResult(
        text=" ".join(results).strip(),
        duration_sec=duration,
        language=lang,
        segments=[],
    )


__all__ = [
    "MAX_BYTES",
    "MAX_DURATION_SEC",
    "SpeechKitAuthError",
    "SpeechKitQuotaExceeded",
    "SpeechResult",
    "UnsupportedFormat",
    "recognize_voice",
]
