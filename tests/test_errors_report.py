"""Error reports: what the owner sees, and what never reaches them."""

from __future__ import annotations

import pytest

from src.errors_report import (
    ErrorReport,
    fingerprint,
    is_transient_noise,
    render_report,
    report_error,
    reset_error_reporter,
)


@pytest.fixture(autouse=True)
def _clean():
    reset_error_reporter()
    yield
    reset_error_reporter()


def _report(**overrides) -> ErrorReport:
    payload = {
        "source": "bot",
        "where": "handlers.intake.on_photo",
        "error": "ValueError: bad portion",
        "traceback": "Traceback (most recent call last):\n  File a\n  File b\nValueError: bad",
        "user": "42 (@tester)",
        "context": {"update": "message", "text": "гречка"},
    }
    payload.update(overrides)
    return ErrorReport(**payload)


def test_the_body_is_one_copy_paste_block():
    text = render_report(_report())
    assert text.count("<pre><code") == 1 and text.count("</code></pre>") == 1
    assert "handlers.intake.on_photo" in text
    assert "42 (@tester)" in text
    assert "ValueError: bad portion" in text


def test_html_in_the_payload_cannot_break_the_markup():
    text = render_report(_report(error="ValueError: <b>oops</b> & co"))
    assert "<b>oops</b>" not in text
    assert "&lt;b&gt;oops&lt;/b&gt;" in text


def test_a_long_traceback_keeps_both_ends():
    frames = "\n".join(f'  File "mod{i}.py", line {i}' for i in range(400))
    text = render_report(_report(traceback=f"Traceback:\n{frames}\nValueError: end"), limit=1200)
    assert "строк пропущено" in text
    assert "mod0.py" in text  # entry point
    assert "ValueError: end" in text  # raise site


def test_the_same_bug_has_the_same_fingerprint():
    assert fingerprint(_report()) == fingerprint(_report(user="7 (@other)"))
    assert fingerprint(_report()) != fingerprint(_report(where="handlers.reports.stats"))


@pytest.mark.parametrize(
    "where,error",
    [
        ("aiogram.polling", "TelegramNetworkError: failed to fetch updates"),
        ("llm.openrouter", "LLMRateLimitError: HTTP 429"),
        ("web", "httpx.ReadTimeout: timed out"),
    ],
)
def test_self_healing_churn_never_wakes_the_owner(where, error):
    assert is_transient_noise(where, error)


def test_a_real_bug_is_not_noise():
    assert not is_transient_noise("handlers.intake.on_photo", "ValueError: bad portion")


@pytest.mark.asyncio
async def test_unwired_reporter_is_a_no_op_not_a_crash():
    assert await report_error(source="bot", where="x", error="ValueError: y") == "disabled"


@pytest.mark.asyncio
async def test_noise_is_dropped_before_delivery():
    assert (
        await report_error(source="bot", where="polling", error="TelegramNetworkError: x")
        == "noise"
    )
