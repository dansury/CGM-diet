"""Repo-root anchored paths for bundled resources (prompts, seeds).

Resource paths must not depend on the process CWD: the container runs from
`/app` but entrypoints may chdir. `APP_ROOT` is the directory holding `src/`
(overridable via `APP_HOME`). See `spec/infra.md` § Resource paths.
"""

from __future__ import annotations

import os
from pathlib import Path


def _detect_root() -> Path:
    env = os.getenv("APP_HOME")
    if env:
        candidate = Path(env).expanduser()
        if (candidate / "src").is_dir():
            return candidate.resolve()
    return Path(__file__).resolve().parents[1]


APP_ROOT: Path = _detect_root()


def repo_path(*parts: str | Path) -> Path:
    path = Path(*parts) if parts else Path()
    return path if path.is_absolute() else APP_ROOT / path


__all__ = ["APP_ROOT", "repo_path"]
