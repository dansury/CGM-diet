"""Environment-driven settings. See `spec/infra.md` § Config & Environment."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # minimal CI
    load_dotenv = None  # type: ignore[assignment]


class ConfigError(RuntimeError):
    """Raised when a required setting is missing or malformed."""


def _read(name: str) -> str | None:
    raw = os.environ.get(name)
    if raw is None:
        return None
    raw = raw.strip()
    return raw or None


def _read_bool(name: str, default: bool) -> bool:
    value = _read(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def _read_int(name: str, default: int) -> int:
    value = _read(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer") from exc


def _read_int_tuple(name: str) -> tuple[int, ...]:
    value = _read(name)
    if value is None:
        return ()
    parts = [p.strip() for p in value.split(",") if p.strip()]
    try:
        return tuple(int(p) for p in parts)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a comma-separated list of integers") from exc


def _read_window(name: str, default: tuple[int, int]) -> tuple[int, int]:
    """Parse a `START-END` minute window, e.g. `45-90`."""
    value = _read(name)
    if value is None:
        return default
    parts = value.replace(" ", "").split("-")
    if len(parts) != 2:
        raise ConfigError(f"{name} must look like '45-90'")
    try:
        start, end = int(parts[0]), int(parts[1])
    except ValueError as exc:
        raise ConfigError(f"{name} must look like '45-90'") from exc
    if start >= end:
        raise ConfigError(f"{name}: start must be < end")
    return start, end


def normalize_database_url(url: str) -> str:
    """Coerce common DATABASE_URL spellings to async SQLAlchemy dialects."""
    replacements = (
        ("postgres+asyncpg://", "postgresql+asyncpg://"),
        ("postgresql+asyncpg://", "postgresql+asyncpg://"),
        ("postgres://", "postgresql+asyncpg://"),
        ("postgresql://", "postgresql+asyncpg://"),
        ("sqlite+aiosqlite://", "sqlite+aiosqlite://"),
        ("sqlite://", "sqlite+aiosqlite://"),
    )
    for prefix, replacement in replacements:
        if url.startswith(prefix):
            return replacement + url[len(prefix) :]
    return url


def ensure_sqlite_parent_dir(url: str) -> None:
    """SQLite never creates the DB file's parent dir — do it here."""
    if not url.startswith("sqlite"):
        return
    try:
        from sqlalchemy.engine import make_url

        database = make_url(url).database
    except Exception:
        return
    if not database or database == ":memory:":
        return
    parent = Path(database).expanduser().parent
    if str(parent) and not parent.exists():
        parent.mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True, slots=True)
class Settings:
    telegram_bot_token: str = ""
    owner_tg_ids: tuple[int, ...] = ()
    database_url: str = "sqlite+aiosqlite:///data/cgm.db"

    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    vision_model: str = "google/gemini-2.5-flash"
    text_model: str = "google/gemini-2.5-flash"
    llm_mock: bool = False

    # Speech-to-text: Yandex SpeechKit first, OpenAI-compatible endpoint as
    # a fallback (`spec/ingest.md` § Голос).
    yandex_speechkit_api_key: str = ""
    yandex_folder_id: str = ""
    speechkit_lang: str = "ru-RU"
    stt_base_url: str = ""
    stt_api_key: str = ""
    stt_model: str = "whisper-1"

    app_env: str = "local"
    bot_mode: str = "polling"
    webhook_base_url: str = ""
    webhook_secret: str = ""
    web_host: str = "0.0.0.0"
    web_port: int = 8080

    default_glucose_unit: str = "mmol/L"
    window_1h: tuple[int, int] = (45, 90)
    window_2h: tuple[int, int] = (90, 150)
    baseline_window: int = 20
    min_observations: int = 3

    health_sync_secret: str = ""
    # где взять APK приложения-моста (`spec/health_sync.md` § Инструкция)
    health_bridge_url: str = "https://github.com/dansury/CGM-diet/releases/latest"

    # error reports to the owner (`spec/errors.md`)
    error_reports_enabled: bool = True
    error_report_tg_ids: tuple[int, ...] = ()

    # free-model fallback on 429 (`spec/models.md`)
    free_fallback_enabled: bool = True

    extras: dict[str, str] = field(default_factory=dict, compare=False)

    def require(self, *keys: str) -> None:
        missing = [k for k in keys if not getattr(self, k, None)]
        if missing:
            raise ConfigError("missing required settings: " + ", ".join(missing))

    @property
    def vision_available(self) -> bool:
        return self.llm_mock or bool(self.openrouter_api_key)

    @property
    def error_recipients(self) -> tuple[int, ...]:
        """Who gets error reports: the explicit list, else the owners."""
        return self.error_report_tg_ids or self.owner_tg_ids

    def is_owner(self, tg_id: int) -> bool:
        return tg_id in self.owner_tg_ids

    @property
    def speechkit_available(self) -> bool:
        """SpeechKit needs both an API key and the folder it is billed to."""
        return bool(self.yandex_speechkit_api_key and self.yandex_folder_id)

    @property
    def stt_available(self) -> bool:
        return (
            self.llm_mock
            or self.speechkit_available
            or bool(self.stt_base_url and self.stt_api_key)
        )


_CACHE: Settings | None = None
_DOTENV_LOADED = False


def _load_dotenv_once(dotenv_path: Path | None) -> None:
    global _DOTENV_LOADED
    if _DOTENV_LOADED or load_dotenv is None:
        return
    if dotenv_path is not None:
        load_dotenv(dotenv_path, override=False)
    else:
        load_dotenv(override=False)
    _DOTENV_LOADED = True


def load_settings(*, dotenv_path: Path | None = None, refresh: bool = False) -> Settings:
    global _CACHE
    if _CACHE is not None and not refresh:
        return _CACHE
    _load_dotenv_once(dotenv_path)
    settings = Settings(
        telegram_bot_token=_read("TELEGRAM_BOT_TOKEN") or "",
        owner_tg_ids=_read_int_tuple("OWNER_TG_IDS"),
        database_url=normalize_database_url(
            _read("DATABASE_URL") or "sqlite+aiosqlite:///data/cgm.db"
        ),
        openrouter_api_key=_read("OPENROUTER_API_KEY") or "",
        openrouter_base_url=_read("OPENROUTER_BASE_URL") or "https://openrouter.ai/api/v1",
        vision_model=_read("VISION_MODEL") or "google/gemini-2.5-flash",
        text_model=_read("TEXT_MODEL") or "google/gemini-2.5-flash",
        llm_mock=_read_bool("LLM_MOCK", False),
        yandex_speechkit_api_key=(
            _read("YANDEX_SPEECHKIT_API_KEY") or _read("YANDEX_API_KEY") or ""
        ),
        yandex_folder_id=_read("YANDEX_FOLDER_ID") or "",
        speechkit_lang=_read("SPEECHKIT_LANG") or "ru-RU",
        stt_base_url=_read("STT_BASE_URL") or "",
        stt_api_key=_read("STT_API_KEY") or "",
        stt_model=_read("STT_MODEL") or "whisper-1",
        app_env=_read("APP_ENV") or "local",
        bot_mode=(_read("BOT_MODE") or "polling").lower(),
        webhook_base_url=_read("WEBHOOK_BASE_URL") or "",
        webhook_secret=_read("WEBHOOK_SECRET") or "",
        web_host=_read("WEB_HOST") or "0.0.0.0",
        web_port=_read_int("WEB_PORT", 8080),
        default_glucose_unit=_read("DEFAULT_GLUCOSE_UNIT") or "mmol/L",
        window_1h=_read_window("WINDOW_1H", (45, 90)),
        window_2h=_read_window("WINDOW_2H", (90, 150)),
        baseline_window=_read_int("BASELINE_WINDOW", 20),
        min_observations=_read_int("MIN_OBSERVATIONS", 3),
        health_sync_secret=_read("HEALTH_SYNC_SECRET") or "",
        health_bridge_url=(
            _read("HEALTH_BRIDGE_URL")
            or "https://github.com/dansury/CGM-diet/releases/latest"
        ),
        error_reports_enabled=_read_bool("ERROR_REPORTS_ENABLED", True),
        error_report_tg_ids=_read_int_tuple("ERROR_REPORT_TG_IDS"),
        free_fallback_enabled=_read_bool("FREE_FALLBACK_ENABLED", True),
    )
    _CACHE = settings
    return settings


def reset_cache() -> None:
    global _CACHE, _DOTENV_LOADED
    _CACHE = None
    _DOTENV_LOADED = False


__all__ = [
    "ConfigError",
    "Settings",
    "ensure_sqlite_parent_dir",
    "load_settings",
    "normalize_database_url",
    "reset_cache",
]
