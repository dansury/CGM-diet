"""Onboarding, menu and settings."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from src.db import repo
from src.handlers.deps import local_now, session_scope
from src.handlers.features import maybe_send_hint, menu_of
from src.keyboards import CANCEL_DATA, main_menu
from src.logging_setup import get_logger
from src.reporting import DISCLAIMER

router = Router(name="common")
log = get_logger("handlers.common")



WELCOME = """👋 Это дневник <b>еда → сахар → самочувствие</b>.

<b>Что можно присылать — в любом виде:</b>
🍽 фото еды или текстом «съела овсянку с бананом»
🩸 скриншот CGM/глюкометра или просто «сахар 8.2»
🏷 фото этикетки (можно две стороны подряд — лицевую и оборотную)
🧪 анализы: фото, PDF или текст
🏃 тренировку: «бегал 40 минут», фото трекера или страницы дневника
⚖️ вес и состав тела: «вес 82,4 жир 24%» или фото экрана весов
🎤 голосовое — распознаю и разберу

<b>Что я делаю:</b>
• связываю приёмы пищи с сахаром по времени (45–90 мин и 90–150 мин);
• считаю, после каких <i>компонентов</i> сахар поднимается чаще и выше;
• показываю это графиком и цифрами с уровнем достоверности;
• спрашиваю о самочувствии и сопоставляю симптомы с сахаром;
• веду вес и состав тела, считаю дневной коридор калорий под вашу цель
  и показываю прогресс-бар после каждого приёма пищи;
• оцениваю каждый приём пищи по Гарвардской тарелке и подсказываю, чего
  добрать сегодня (/plate, отключается в настройках);
• храню анализы с референсами из ваших документов и называю продукты-источники
  по маркерам вне референса (/labs).

<b>Чего я не делаю:</b> не ставлю диагнозы, не назначаю лекарства и не считаю дозы.

Команды: /today /stats /graph /plate /labs /body /workout /wellbeing /health /export /delete /help"""

HELP = """<b>Как пользоваться</b>

📷 <b>Фото</b> — просто пришлите. Я сам определю, что это: еда, экран CGM,
этикетка или анализы. Если ошибусь — нажмите «Это не еда» и выберите тип.

🛒 <b>Проверить перед покупкой</b> — нажмите «🛒 Проверить продукт» и пришлите
фото упаковки. Отвечу по вашей собственной статистике, ничего не записывая
в дневник как съеденное.

🏷 <b>Две стороны упаковки</b> — отправьте оба фото одним альбомом (или нажмите
«➕ Вторая сторона»), я соберу их в одну карточку продукта.

✍️ <b>Текст</b> — «сахар 8», «глюкоза 4.5 ммоль натощак», «вес 72,3»,
«вчера в 21:00 сахар 9.1». Понимаю время и единицы.

🙂 <b>Самочувствие</b> — оценка 1–5; если не 5, предложу симптомы кнопками из
вашего личного глоссария. Можно добавить свои или наговорить голосом — то, что
вы написали сами, встанет первой кнопкой и уже отмеченным.

⭐️ <b>Мой словарь</b> (/my) — всё, что вы записываете повторно: блюда,
продукты, упаковки, лекарства и самочувствие. Сверху всегда последнее.

❌ <b>Отменить</b> — крестик есть на любой карточке и в любом приглашении
что-то написать; то же делает /cancel.

⚖️ <b>Вес и цель</b> (/body) — рост, возраст, пол, биоимпеданс и целевой вес.
Задайте цель — покажу дневной коридор калорий с безопасным темпом снижения
(не быстрее 1 % массы тела в неделю) и полосу прогресса после каждой еды.
Раз в две недели сам напомню взвеситься; вес можно вводить и когда захочется —
«вес 82,4», /weight или фото весов.

🏃 <b>Тренировки</b> (/workout) — «бегал 40 минут», голосом, фото трекера или
рукописного дневника. Уточню длительность, интенсивность и вспотели ли вы —
и посчитаю примерные энергозатраты.

🥗 <b>Гарвардская тарелка</b> (/plate) — после каждого фото еды показываю доли
тарелки (½ овощи и фрукты, ¼ цельные злаки, ¼ белок) и что добрать до конца дня.
Приём пищи собирается из всех блюд подряд примерно за час — точнее, за ваше
собственное среднее время еды. Число приёмов в день задаётся
(<code>/set meals 4</code>) или считается по вашей статистике.

🧪 <b>Анализы</b> (/labs) — фото, PDF или текст результата. Сохраню маркеры с
референсами <i>из самого документа</i>, отмечу, что вне нормы, и назову
продукты-источники нужного нутриента. Это не расшифровка: диагноз и БАДы — к врачу.

🙈 <b>Скрытые возможности</b> (/hidden) — то, что вы убрали кнопкой «Не нужно».
Оттуда же можно вернуть обратно в меню.

📊 /stats — статистика по компонентам · /graph — график · /today — сегодня
📤 /export — выгрузка CSV · 🗑 /delete — удалить все данные
⌚️ /health — подключение Samsung Health

""" + DISCLAIMER


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    async with session_scope() as session:
        user = await repo.get_or_create_user(
            session,
            message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
        )
        if user.consent_at is None:
            user.consent_at = local_now(user)
        user.onboarded = True
        hidden = await repo.hidden_features(session, user)
    await message.answer(WELCOME, reply_markup=main_menu(hidden))
    # Одна возможность про запас — при старте и потом раз в неделю
    # (`spec/features.md`). Сбой подсказки не имеет права сорвать онбординг.
    if message.bot is not None:
        try:
            await maybe_send_hint(message.bot, message.chat.id, first=True)
        except Exception:
            log.exception("start hint failed")


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP, reply_markup=await menu_of(message.chat.id))


@router.message(Command("menu"))
@router.message(F.text == "◀️ Меню")
async def cmd_menu(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Главное меню", reply_markup=await menu_of(message.chat.id))


CANCELLED = "❌ Отменено. Ничего не записал."


@router.callback_query(F.data == CANCEL_DATA)
async def on_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    """Крестик на любой карточке и в любой подсказке — один и тот же выход.

    Отмена не спрашивает подтверждения и ничего не пишет в дневник: черновик
    живёт в FSM, а `state.clear()` уносит его целиком.
    """
    await state.clear()
    await callback.answer("Отменено")
    if callback.message is not None:
        await callback.message.edit_text(CANCELLED)


@router.message(Command("cancel"))
@router.message(F.text == "❌ Отменить")
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    """То же самое текстом — на случай, если карточка уехала вверх по чату."""
    await state.clear()
    await message.answer(CANCELLED, reply_markup=await menu_of(message.chat.id))


@router.message(Command("settings"))
async def cmd_settings(message: Message) -> None:
    async with session_scope() as session:
        user = await repo.get_or_create_user(session, message.from_user.id)
        profile = await repo.get_body_profile(session, user)
        days = profile.weight_prompt_days if profile else 14
        meals = user.meals_per_day or "по статистике"
        text = (
            "⚙️ <b>Настройки</b>\n"
            f"Часовой пояс: <code>{user.tz}</code>\n"
            f"Единицы глюкозы: <code>{user.glucose_unit}</code>\n"
            f"Окно «через 1 час»: {user.window_1h_start}–{user.window_1h_end} мин\n"
            f"Окно «через 2 часа»: {user.window_2h_start}–{user.window_2h_end} мин\n"
            f"Базовая линия до еды: {user.baseline_window} мин\n"
            f"Напоминать взвеситься: раз в {days} дн.\n"
            f"Гарвардская тарелка: {'вкл' if user.plate_enabled else 'выкл'}\n"
            f"Приёмов пищи в день: {meals}\n\n"
            "Изменить: <code>/set tz Europe/Moscow</code>, "
            "<code>/set unit mg/dL</code>, <code>/set window1 45-90</code>, "
            "<code>/set window2 90-150</code>, <code>/set baseline 20</code>, "
            "<code>/set weighin 14</code>, <code>/set plate on|off</code>, "
            "<code>/set meals 3|auto</code>"
        )
    await message.answer(text)


@router.message(Command("set"))
async def cmd_set(message: Message) -> None:
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) < 3:
        await message.answer("Формат: <code>/set tz Europe/Moscow</code>")
        return
    key, value = parts[1].lower(), parts[2].strip()
    async with session_scope() as session:
        user = await repo.get_or_create_user(session, message.from_user.id)
        if key == "weighin":
            # частота напоминаний о взвешивании живёт в профиле тела
            try:
                days = int(value)
            except ValueError:
                await message.answer("Формат: <code>/set weighin 14</code> (дни)")
                return
            if not 3 <= days <= 90:
                await message.answer("Напоминать можно раз в 3–90 дней.")
                return
            await repo.upsert_body_profile(session, user, weight_prompt_days=days)
            await message.answer(f"✅ напоминание о взвешивании: раз в {days} дн.")
            return
        try:
            applied = _apply_setting(user, key, value)
        except ValueError as exc:
            await message.answer(f"Не понял значение: {exc}")
            return
    await message.answer(f"✅ {applied}")


def _apply_setting(user, key: str, value: str) -> str:
    """Validate and apply one setting; raises ValueError with a readable reason."""
    if key == "tz":
        try:
            from zoneinfo import ZoneInfo

            ZoneInfo(value)
        except Exception as exc:
            raise ValueError(f"неизвестный часовой пояс {value}") from exc
        user.tz = value
        return f"часовой пояс: {value}"
    if key == "unit":
        from src.ingest.units import MGDL, MMOL, canonical_unit

        unit = canonical_unit(value)
        if unit not in (MMOL, MGDL):
            raise ValueError("нужно mmol/L или mg/dL")
        user.glucose_unit = unit
        return f"единицы: {unit}"
    if key in {"window1", "window2"}:
        start, end = _parse_window(value)
        if key == "window1":
            user.window_1h_start, user.window_1h_end = start, end
        else:
            user.window_2h_start, user.window_2h_end = start, end
        return f"окно {key}: {start}–{end} мин"
    if key == "plate":
        # Гарвардская тарелка отключается целиком (`spec/plate.md`)
        if value.lower() in {"on", "вкл", "да", "1", "true"}:
            user.plate_enabled = True
            return "оценка тарелки: включена"
        if value.lower() in {"off", "выкл", "нет", "0", "false"}:
            user.plate_enabled = False
            return "оценка тарелки: выключена"
        raise ValueError("нужно on или off")
    if key == "meals":
        from src.analytics.plate import MAX_MEALS_PER_DAY, MIN_MEALS_PER_DAY

        if value.lower() in {"auto", "авто", "0"}:
            user.meals_per_day = None
            return "приёмов пищи в день: по вашей статистике"
        count = int(value)
        if not MIN_MEALS_PER_DAY <= count <= MAX_MEALS_PER_DAY:
            raise ValueError(f"приёмов пищи в день — от {MIN_MEALS_PER_DAY} до {MAX_MEALS_PER_DAY}")
        user.meals_per_day = count
        return f"приёмов пищи в день: {count}"
    if key == "baseline":
        minutes = int(value)
        if not 5 <= minutes <= 60:
            raise ValueError("базовая линия — от 5 до 60 минут")
        user.baseline_window = minutes
        return f"базовая линия: {minutes} мин"
    raise ValueError(f"неизвестный параметр {key}")


def _parse_window(value: str) -> tuple[int, int]:
    parts = value.replace(" ", "").split("-")
    if len(parts) != 2:
        raise ValueError("формат окна: 45-90")
    start, end = int(parts[0]), int(parts[1])
    if not 0 <= start < end <= 360:
        raise ValueError("окно должно быть от 0 до 360 минут и start < end")
    return start, end


__all__ = ["CANCELLED", "HELP", "WELCOME", "router"]
