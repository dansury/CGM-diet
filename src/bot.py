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
    BotCommand(command="today", description="Записи за сегодня"),
    BotCommand(command="stats", description="Статистика по продуктам"),
    BotCommand(command="graph", description="График еды и сахара"),
    BotCommand(command="wellbeing", description="Отметить самочувствие"),
    BotCommand(command="check", description="Проверить продукт перед покупкой"),
    BotCommand(command="health", description="Подключить Samsung Health"),
    BotCommand(command="export", description="Выгрузить данные (CSV)"),
    BotCommand(command="delete", description="Удалить все данные"),
    BotCommand(command="settings", description="Настройки"),
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


async def run_polling(settings: Settings | None = None) -> None:
    s = settings or load_settings()
    bot = build_bot(s)
    dispatcher = build_dispatcher()
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
