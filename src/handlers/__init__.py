"""Router registry. Order matters: specific routers before the catch-all text."""

from __future__ import annotations

from aiogram import Router


def build_router() -> Router:
    from src.handlers import (
        admin,
        common,
        confirm,
        dictionary,
        errors,
        intake,
        meds,
        reports,
        wellbeing,
    )

    root = Router(name="root")
    root.include_router(admin.router)  # owner-only, filtered; falls through otherwise
    root.include_router(common.router)
    root.include_router(reports.router)
    root.include_router(wellbeing.router)
    root.include_router(dictionary.router)
    root.include_router(meds.router)
    root.include_router(confirm.router)
    root.include_router(intake.router)  # catch-all text/photo last
    root.include_router(errors.router)  # error observer, no message handlers
    return root


__all__ = ["build_router"]
