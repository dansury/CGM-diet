"""Хранилище: где живут данные и когда бот обязан кричать об их потере."""

from __future__ import annotations

from src.config import Settings, resolve_sqlite_url, sqlite_path
from src.db.persistence import describe_storage
from src.paths import APP_ROOT


def test_a_relative_sqlite_path_is_anchored_to_the_app_root():
    """Иначе запуск из другого каталога открывает пустую базу вместо настоящей."""
    resolved = resolve_sqlite_url("sqlite+aiosqlite:///data/cgm.db")
    assert sqlite_path(resolved) == APP_ROOT / "data" / "cgm.db"


def test_an_absolute_path_and_other_dialects_are_left_alone():
    absolute = "sqlite+aiosqlite:////var/lib/cgm/cgm.db"
    assert resolve_sqlite_url(absolute) == absolute
    postgres = "postgresql+asyncpg://u:p@db/cgm"
    assert resolve_sqlite_url(postgres) == postgres


def test_sqlite_without_a_mount_in_a_deployed_env_is_reported_as_ephemeral(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'cgm.db'}"
    report = describe_storage(
        Settings(database_url=url, app_env="staging"), is_mounted=lambda _: False
    )
    assert report.kind == "sqlite"
    assert report.ephemeral
    assert not report.durable


def test_local_development_is_not_a_data_loss_warning(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'cgm.db'}"
    report = describe_storage(
        Settings(database_url=url, app_env="local"), is_mounted=lambda _: False
    )
    assert report.durable


def test_a_mounted_volume_makes_sqlite_durable(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'cgm.db'}"
    report = describe_storage(
        Settings(database_url=url, app_env="staging"), is_mounted=lambda _: True
    )
    assert report.durable


def test_postgres_is_durable_and_its_password_never_leaves_the_report():
    report = describe_storage(
        Settings(database_url="postgresql+asyncpg://u:secret@db/cgm", app_env="staging")
    )
    assert report.kind == "postgresql"
    assert report.durable
    assert "secret" not in report.location
