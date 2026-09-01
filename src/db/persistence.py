"""Startup check: will this database survive the next redeploy?

Nothing in the app deletes a user's profile, weights or goals on its own, so
"the bot forgot my weight after the update" always means the *store* was
replaced: a SQLite file inside the container filesystem, a relative path opened
from another working directory, a fresh anonymous volume. Those failures are
silent — the bot starts clean and greets the owner as a new user.

This module makes them loud: one report at startup, an ERROR (which reaches the
owner through `errors_report`) when the data is sitting on disposable storage,
and a hint pointing at the older database file when one is found nearby.
See `spec/infra.md` § Хранение данных.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from src.config import Settings, sqlite_path
from src.logging_setup import get_logger
from src.paths import repo_path

log = get_logger("db.persistence")

#: where a stray database from an earlier layout / working directory may hide
STRAY_DIRS = ("data", ".", "/app/data", "/data")


@dataclass(frozen=True, slots=True)
class StorageReport:
    kind: str  # sqlite | postgresql | other
    location: str  # file path for sqlite, host/db for the rest
    exists: bool
    size_bytes: int
    ephemeral: bool  # lives in the container fs, nothing mounted underneath
    strays: tuple[str, ...]  # other non-empty .db files found nearby

    @property
    def durable(self) -> bool:
        return not self.ephemeral


def _safe_location(url: str) -> str:
    """Connection target without the password."""
    try:
        from sqlalchemy.engine import make_url

        return make_url(url).render_as_string(hide_password=True)
    except Exception:
        return url.split("@")[-1]


ROOT = Path("/")


def _is_mounted(path: Path) -> bool:
    """True when a volume is mounted at `path` or at one of its parents.

    The root filesystem does not count: in a container it *is* the disposable
    layer, so a file directly on it is exactly the case we are looking for.
    """
    current = path.resolve()
    for candidate in (current, *current.parents):
        if candidate == ROOT:
            break
        try:
            if os.path.ismount(candidate):
                return True
        except OSError:
            return False
    return False


def _strays(configured: Path) -> tuple[str, ...]:
    found: list[str] = []
    for raw in STRAY_DIRS:
        directory = Path(raw) if Path(raw).is_absolute() else repo_path(raw)
        try:
            candidates = sorted(directory.glob("*.db"))
        except OSError:
            continue
        for candidate in candidates:
            try:
                if candidate.resolve() == configured.resolve() or candidate.stat().st_size == 0:
                    continue
            except OSError:
                continue
            if str(candidate) not in found:
                found.append(str(candidate))
    return tuple(found)


def describe_storage(settings: Settings, *, is_mounted=_is_mounted) -> StorageReport:
    url = settings.database_url
    path = sqlite_path(url)
    if path is None:
        kind = "postgresql" if url.startswith("postgresql") else "other"
        return StorageReport(
            kind=kind,
            location=_safe_location(url),
            exists=True,
            size_bytes=0,
            ephemeral=False,
            strays=(),
        )
    exists = path.exists()
    size = path.stat().st_size if exists else 0
    # Local development runs from a checkout — the file is as durable as the
    # laptop. Ephemeral storage is only a problem for a deployed container.
    ephemeral = settings.app_env != "local" and not is_mounted(path.parent)
    return StorageReport(
        kind="sqlite",
        location=str(path),
        exists=exists,
        size_bytes=size,
        ephemeral=ephemeral,
        strays=_strays(path),
    )


def check_persistence(settings: Settings, *, is_mounted=_is_mounted) -> StorageReport:
    """Log where user data lives; shout when it is about to be thrown away."""
    report = describe_storage(settings, is_mounted=is_mounted)
    log.info(
        "storage: %s %s (exists=%s, %.1f KB)",
        report.kind,
        report.location,
        report.exists,
        report.size_bytes / 1024,
    )
    if report.ephemeral:
        log.error(
            "DATA LOSS RISK: SQLite file %s is inside the container filesystem — "
            "every redeploy starts from an empty database. Mount a volume at %s "
            "(docker: -v cgm_data:/app/data) or point DATABASE_URL at Postgres.",
            report.location,
            Path(report.location).parent,
        )
    if report.strays and report.size_bytes == 0:
        log.error(
            "storage %s is empty, but another database file exists: %s — "
            "the bot is probably opening the wrong path (DATABASE_URL / working dir).",
            report.location,
            ", ".join(report.strays),
        )
    return report


__all__ = ["StorageReport", "check_persistence", "describe_storage"]
