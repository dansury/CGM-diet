"""FastAPI application: Telegram webhook + Health Connect relay + /health."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from src.config import Settings, load_settings
from src.db import repo
from src.db.engine import get_engine
from src.errors_report import report_error
from src.handlers.deps import session_scope
from src.health.samsung import HealthSyncError, parse_samples, verify_token
from src.logging_setup import get_logger, setup_logging

log = get_logger("web")


def _request_context(request: Request) -> dict[str, str]:
    client = request.client.host if request.client else "?"
    return {
        "query": str(request.url.query)[:200],
        "client": client,
        "ua": request.headers.get("user-agent", "")[:200],
    }


def create_app(settings: Settings | None = None) -> FastAPI:
    setup_logging()
    s = settings or load_settings()
    app = FastAPI(title="CGM-diet", version="0.1.0", docs_url=None, redoc_url=None)

    @app.middleware("http")
    async def error_report_middleware(request: Request, call_next: Any) -> Any:
        """4xx are client mistakes; 5xx and crashes are ours (`spec/errors.md`)."""
        try:
            response = await call_next(request)
        except HTTPException as exc:
            if exc.status_code >= 500:
                await report_error(
                    source="web",
                    where=f"{request.method} {request.url.path}",
                    exc=exc,
                    context=_request_context(request),
                )
            raise
        except Exception as exc:
            await report_error(
                source="web",
                where=f"{request.method} {request.url.path}",
                exc=exc,
                context=_request_context(request),
            )
            raise
        return response

    bot = None
    dispatcher = None
    if s.telegram_bot_token:
        from src.bot import COMMANDS, build_bot, build_dispatcher

        bot = build_bot(s)
        dispatcher = build_dispatcher()

        @app.on_event("startup")
        async def _startup() -> None:  # pragma: no cover - network
            from src.bot import prepare_runtime

            await prepare_runtime(bot, s)
            await bot.set_my_commands(COMMANDS)
            if s.webhook_base_url:
                url = f"{s.webhook_base_url.rstrip('/')}/telegram/webhook"
                await bot.set_webhook(
                    url, secret_token=s.webhook_secret or None, drop_pending_updates=False
                )
                log.info("webhook set to %s", url)

        @app.on_event("shutdown")
        async def _shutdown() -> None:  # pragma: no cover - network
            await bot.session.close()

    @app.get("/health")
    async def health() -> JSONResponse:
        db_ok, detail = True, "ok"
        try:
            engine = get_engine()
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        except Exception as exc:
            db_ok, detail = False, str(exc)[:200]
        from src.db.persistence import describe_storage

        storage = describe_storage(s)
        payload = {
            "status": "ok" if db_ok else "degraded",
            "env": s.app_env,
            "db": {
                "ok": db_ok,
                "detail": detail,
                "kind": storage.kind,
                "durable": storage.durable,
            },
            "llm": {"mock": s.llm_mock, "configured": bool(s.openrouter_api_key)},
            "bot_mode": s.bot_mode,
        }
        return JSONResponse(payload, status_code=200 if db_ok else 503)

    @app.post("/telegram/webhook")
    async def telegram_webhook(
        request: Request,
        x_telegram_bot_api_secret_token: str | None = Header(default=None),
    ) -> dict[str, str]:
        if bot is None or dispatcher is None:
            raise HTTPException(status_code=503, detail="bot is not configured")
        if s.webhook_secret and x_telegram_bot_api_secret_token != s.webhook_secret:
            raise HTTPException(status_code=403, detail="bad secret token")
        from aiogram.types import Update

        update = Update.model_validate(await request.json(), context={"bot": bot})
        await dispatcher.feed_update(bot, update)
        return {"ok": "true"}

    @app.post("/health/samsung")
    async def samsung_sync(
        request: Request, x_health_token: str | None = Header(default=None)
    ) -> dict[str, Any]:
        """Relay endpoint for the phone-side Health Connect bridge."""
        payload = await request.json()
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="object expected")
        try:
            tg_id = int(payload.get("tg_id") or 0)
        except (TypeError, ValueError):
            tg_id = 0
        if not tg_id:
            raise HTTPException(status_code=400, detail="tg_id is required")
        if not verify_token(tg_id, x_health_token or "", s.health_sync_secret):
            raise HTTPException(status_code=403, detail="bad token")
        try:
            samples = parse_samples(payload)
        except HealthSyncError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        async with session_scope() as session:
            user = await repo.get_or_create_user(session, tg_id)
            inserted = await repo.upsert_activity(session, user, samples)
        return {"accepted": inserted, "received": len(samples)}

    return app


__all__ = ["create_app"]
