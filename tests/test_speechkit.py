"""Yandex SpeechKit: OGG chunking and the failure modes the handler branches on."""

from __future__ import annotations

import struct

import httpx
import pytest

from src.ingest import speechkit


def _ogg_page(*, granule: int, seqno: int, header_type: int, body: bytes = b"\x00" * 32) -> bytes:
    page = bytearray(b"OggS")
    page.append(0)                       # version
    page.append(header_type)
    page += struct.pack("<q", granule)
    page += struct.pack("<I", 1)         # serial
    page += struct.pack("<I", seqno)
    page += struct.pack("<I", 0)         # crc
    segments = [255] * (len(body) // 255) + [len(body) % 255]
    page.append(len(segments))
    page += bytes(segments)
    page += body
    return bytes(page)


def _stream(body_pages: int, *, granules_per_page: int) -> bytes:
    data = _ogg_page(granule=0, seqno=0, header_type=0x02)      # BOS (OpusHead)
    data += _ogg_page(granule=0, seqno=1, header_type=0x00)     # OpusTags
    for n in range(1, body_pages + 1):
        data += _ogg_page(
            granule=n * granules_per_page,
            seqno=n + 1,
            header_type=0x00,
            body=b"\x11" * 200,
        )
    return data


def test_parses_every_page_of_a_stream():
    pages = speechkit._parse_ogg_pages(_stream(3, granules_per_page=48000))
    assert len(pages) == 5
    assert pages[0]["header_type"] & 0x02  # BOS survives the round trip


def test_a_long_voice_is_split_into_standalone_streams():
    # 10 pages × 10 s ≫ the 25 s per-request limit
    chunks = speechkit._chunk_ogg(_stream(10, granules_per_page=10 * 48000))
    assert len(chunks) > 1
    for chunk in chunks:
        assert chunk.startswith(b"OggS")
        pages = speechkit._parse_ogg_pages(chunk)
        # every chunk carries the two header pages, renumbered from zero
        assert pages[0]["header_type"] & 0x02
        assert [p["seqno"] for p in pages] == list(range(len(pages)))
    assert speechkit._parse_ogg_pages(chunks[-1])[-1]["header_type"] & 0x04  # EOS


async def test_a_short_voice_is_sent_as_one_request():
    audio = _stream(2, granules_per_page=48000)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"result": "гречка двести грамм"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    result = await speechkit.recognize_voice(audio, api_key="k", folder_id="f", client=client)
    assert result.text == "гречка двести грамм"
    assert len(seen) == 1
    assert seen[0].url.params["lang"] == "ru-RU"
    assert seen[0].url.params["folderId"] == "f"
    assert seen[0].headers["Authorization"] == "Api-Key k"


async def test_chunk_texts_are_joined_in_order():
    audio = _stream(10, granules_per_page=10 * 48000)
    answers = iter(["первая часть", "вторая часть", "третья часть", "четвёртая"])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"result": next(answers)})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    result = await speechkit.recognize_voice(audio, api_key="k", folder_id="f", client=client)
    assert result.text.startswith("первая часть вторая часть")


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (401, speechkit.SpeechKitAuthError),
        (403, speechkit.SpeechKitAuthError),
        (429, speechkit.SpeechKitQuotaExceeded),
    ],
)
async def test_auth_and_quota_are_distinct_errors(status, error):
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(status, text="nope"))
    )
    with pytest.raises(error):
        await speechkit.recognize_voice(
            _stream(1, granules_per_page=48000),
            api_key="k",
            folder_id="f",
            client=client,
        )


async def test_a_server_error_yields_empty_text_rather_than_a_crash():
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(500, text="boom"))
    )
    result = await speechkit.recognize_voice(
        _stream(1, granules_per_page=48000), api_key="k", folder_id="f", client=client
    )
    assert result.text == ""


@pytest.mark.parametrize("payload", [b"", b"RIFFxxxxWAVE"])
async def test_non_ogg_input_is_rejected(payload):
    with pytest.raises(speechkit.UnsupportedFormat):
        await speechkit.recognize_voice(payload, api_key="k", folder_id="f")
