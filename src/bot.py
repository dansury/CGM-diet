"""Entry point: aiogram 3 dispatcher, polling or webhook.

`BOT_MODE=polling` (default) runs long polling — the zero-infrastructure mode.
`BOT_MODE=webhook` starts the FastAPI app from `src.web.app`, which serves both
the Telegram webhook and the Samsung Health relay endpoint.
See `spec/bot.md` § Runtime.
"""

from __future__ import annotations

import asyncio

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

from src.config import ConfigError, Settings, load_settings
from src.handlers import build_router
from src.logging_setup import get_logger, setup_logging

log = get_logger("bot")

COMMANDS = [
    BotCommand(command="start", description="Начало и инструкция"),
    BotCommand(command="my", description="Личный словарь — запись одной кнопкой"),
    BotCommand(command="meds", description="Лекарства: журнал и справка"),
    BotCommand(command="today", description="Записи за сегодня"),
    BotCommand(command="stats", description="Статистика по продуктам"),
    BotCommand(command="graph", description="График еды и сахара"),
    BotCommand(command="plate", description="Гарвардская тарелка: оценка и настройки"),
    BotCommand(command="labs", description="Анализы и продукты-источники"),
    BotCommand(command="wellbeing", description="Отметить самочувствие"),
    BotCommand(command="body", description="Вес, состав тела и цель"),
    BotCommand(command="weight", description="Записать вес"),
    BotCommand(command="workout", description="Записать тренировку"),
    BotCommand(command="check", description="Проверить продукт перед покупкой"),
    BotCommand(command="health", description="Подключить Samsung Health"),
    BotCommand(command="export", description="Выгрузить данные (CSV)"),
    BotCommand(command="delete", description="Удалить все данные"),
    BotCommand(command="cancel", description="Отменить текущий ввод"),
    BotCommand(command="settings", description="Настройки"),
    BotCommand(command="hidden", description="Скрытые возможности"),
    BotCommand(command="help", description="Справка"),
]


def build_bot(settings: Settings | None = None) -> Bot:
    s = settings or load_settings()
    if not s.telegram_bot_token:
        raise ConfigError("TELEGRAM_BOT_TOKEN is not set")
    return Bot(
        token=s.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


def build_dispatcher() -> Dispatcher:
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.include_router(build_router())
    return dispatcher


async def prepare_runtime(bot: Bot, settings: Settings) -> None:
    """Everything that must be live before the first update is served.

    Error reports need a Bot to send through; the model cache must be filled
    from the DB (`spec/models.md`), and the free-model catalogue feeds the 429
    fallback chain. All three degrade quietly — none of them may keep the bot
    from starting.
    """
    from src.errors_report import wire_error_reporter

    wire_error_reporter(bot, settings)

    from src.handlers.admin import load_active_models

    try:
        mapping = await load_active_models()
        log.info("active models: %s", mapping)
    except Exception:
        log.warning("could not load model selection; falling back to .env", exc_info=True)

    from src.scheduler import start_scheduler

    start_scheduler(bot)

    if settings.free_fallback_enabled and not settings.llm_mock:
        from src.llm import set_free_alternates
        from src.llm.free_catalog import load_free_models

        try:
            free = await load_free_models()
            set_free_alternates([m.id for m in free[:4]])
            log.info("free-model fallback: %d candidates", len(free))
        except Exception:
            log.warning("free-model catalogue unavailable; no 429 fallback", exc_info=True)


async def run_polling(settings: Settings | None = None) -> None:
    s = settings or load_settings()
    bot = build_bot(s)
    dispatcher = build_dispatcher()
    await prepare_runtime(bot, s)
    await bot.set_my_commands(COMMANDS)
    await bot.delete_webhook(drop_pending_updates=False)
    log.info("starting long polling (env=%s, mock=%s)", s.app_env, s.llm_mock)
    try:
        await dispatcher.start_polling(bot, allowed_updates=dispatcher.resolve_used_update_types())
    finally:
        await bot.session.close()


def main() -> None:
    setup_logging()
    settings = load_settings()
    if not settings.telegram_bot_token:
        # A bare traceback here is the first thing a new operator sees; say what
        # to do instead.
        log.error(
            "TELEGRAM_BOT_TOKEN is not set. Copy .env.example to .env and put the "
            "token from @BotFather there, or export TELEGRAM_BOT_TOKEN."
        )
        raise SystemExit(1)
    if settings.bot_mode == "webhook":
        import uvicorn

        from src.web.app import create_app

        uvicorn.run(
            create_app(settings),
            host=settings.web_host,
            port=settings.web_port,
            log_config=None,
        )
        return
    asyncio.run(run_polling(settings))


if __name__ == "__main__":
    main()
