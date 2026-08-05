import asyncio
import logging
import os
from contextlib import suppress

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError
from aiogram.filters import CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    ErrorEvent,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.utils.chat_action import ChatActionSender
from dotenv import load_dotenv

import automation_map
import quota
from quiz import QUIZ
from storage import FileStorage

load_dotenv()
logging.basicConfig(level=logging.INFO)

ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID", "")
CONTACT_URL = "https://t.me/aiarchitectorrr"
# Каждая собранная карта — оплаченный вызов модели, а квиз открыт всему интернету.
DAILY_LIMIT = int(os.environ.get("DAILY_LIMIT", 3))

dp = Dispatcher(storage=FileStorage())

WELCOME = (
    "👋 Привет! Покажу, <b>что в твоём бизнесе стоит автоматизировать первым</b>.\n\n"
    "7 коротких вопросов (~2 минуты) — и получишь <b>персональную карту автоматизаций</b>: "
    "где утекает время, что автоматизировать и с чего начать.\n\n"
    "Погнали?"
)


class Quiz(StatesGroup):
    active = State()


def progress_bar(step: int, total: int) -> str:
    return "▰" * (step + 1) + "▱" * (total - step - 1) + f"  {step + 1}/{total}"


def question_keyboard(step: int) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=opt, callback_data=f"ans:{i}")]
        for i, opt in enumerate(QUIZ[step]["options"])
    ]
    if step > 0:
        rows.append([InlineKeyboardButton(text="⬅ Назад", callback_data="back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def start_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🚀 Пройти квиз", callback_data="begin")]]
    )


def finish_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✍️ Написать эксперту", url=CONTACT_URL)],
            [InlineKeyboardButton(text="🔄 Пройти заново", callback_data="begin")],
        ]
    )


def chat_message(cb: CallbackQuery) -> Message | None:
    """У сообщений старше 48 часов Telegram отдаёт заглушку без содержимого — отвечать в
    них нельзя, а обращение к её полям падает."""
    return cb.message if isinstance(cb.message, Message) else None


async def restart_hint(cb: CallbackQuery):
    await cb.answer("Этот квиз уже устарел. Начни заново: /start", show_alert=True)


async def show_question(message: Message, state: FSMContext, step: int):
    await state.update_data(step=step)
    q = QUIZ[step]
    text = f"{progress_bar(step, len(QUIZ))}\n\n<b>{q['q']}</b>"
    if q.get("hint"):
        text += f"\n<i>{q['hint']}</i>"
    await message.answer(text, reply_markup=question_keyboard(step))


@dp.message(CommandStart())
async def start(message: Message, command: CommandObject, state: FSMContext):
    await state.set_data({"answers": {}, "source": command.args or "—"})
    await message.answer(WELCOME, reply_markup=start_keyboard())


@dp.callback_query(F.data == "begin")
async def begin(cb: CallbackQuery, state: FSMContext):
    message = chat_message(cb)
    if message is None:
        await restart_hint(cb)
        return
    data = await state.get_data()
    await state.set_state(Quiz.active)
    await state.set_data({"answers": {}, "source": data.get("source", "—")})
    await show_question(message, state, 0)
    await cb.answer()


@dp.callback_query(Quiz.active, F.data == "back")
async def back(cb: CallbackQuery, state: FSMContext):
    message = chat_message(cb)
    data = await state.get_data()
    if message is None or "step" not in data:
        await restart_hint(cb)
        return
    await message.edit_reply_markup(reply_markup=None)
    await cb.answer()
    await show_question(message, state, max(0, data["step"] - 1))


@dp.callback_query(Quiz.active, F.data.startswith("ans:"))
async def on_answer(cb: CallbackQuery, state: FSMContext):
    message = chat_message(cb)
    data = await state.get_data()
    if message is None or "step" not in data:
        await restart_hint(cb)
        return

    step = data["step"]
    options = QUIZ[step]["options"]
    choice = cb.data.split(":", 1)[1]
    # callback_data приходит от клиента, а не из нашей клавиатуры: подделать её можно.
    if not choice.isdigit() or int(choice) >= len(options):
        await cb.answer()
        return

    answers = data["answers"]
    answers[QUIZ[step]["key"]] = options[int(choice)]
    await state.update_data(answers=answers)
    await message.edit_reply_markup(reply_markup=None)
    await cb.answer()
    if step + 1 < len(QUIZ):
        await show_question(message, state, step + 1)
    else:
        await finish(cb, message, state)


async def finish(cb: CallbackQuery, message: Message, state: FSMContext):
    data = await state.get_data()
    answers = data["answers"]
    source = data.get("source", "—")

    try:
        quota.take(cb.from_user.id, DAILY_LIMIT)
    except quota.Exhausted as e:
        await message.answer(
            f"Карту в сутки собираю {e.limit} раза — на сегодня всё. "
            "Ответы я записал, приходи завтра 🙏",
            reply_markup=finish_keyboard(),
        )
        await notify_lead(message.bot, cb.from_user, answers, source)
        await state.clear()
        return

    await message.answer("✨ Собираю твою карту автоматизаций…")
    try:
        async with ChatActionSender.typing(bot=message.bot, chat_id=message.chat.id):
            text = await automation_map.build_map(answers)
    except Exception:
        logging.exception("build_map failed")
        await message.answer(
            "Не получилось собрать карту — ИИ временно недоступен. "
            "Нажми «Пройти заново» чуть позже 🙏",
            reply_markup=finish_keyboard(),
        )
        await notify_lead(message.bot, cb.from_user, answers, source)
        await state.clear()
        return
    await message.answer(text, parse_mode=None)
    await message.answer(
        "Понравилось? Такую автоматизацию я могу <b>внедрить под ключ</b> за тебя — "
        "напиши мне, разберём твой случай 👇",
        reply_markup=finish_keyboard(),
    )
    await notify_lead(message.bot, cb.from_user, answers, source)
    await state.clear()


async def notify_lead(bot: Bot, user, answers: dict, source: str):
    """Лид дороже ответа: если админский чат недоступен, человек не должен об этом узнать,
    а лид обязан остаться хотя бы в логах."""
    who = f"@{user.username}" if user.username else f"{user.first_name} (id {user.id})"
    lines = ["🔥 <b>Новый лид из квиза</b>", f"От: {who}", f"Источник: {source}", ""]
    lines += [f"• {item['q']} — <b>{answers.get(item['key'], '—')}</b>" for item in QUIZ]
    body = "\n".join(lines)
    try:
        await bot.send_message(ADMIN_CHAT_ID, body)
    except TelegramAPIError:
        logging.exception("не доставил лид в админский чат:\n%s", body)


@dp.errors()
async def on_error(event: ErrorEvent) -> bool:
    """Исключение в хендлере не должно ронять polling и не должно оставлять человека
    молча смотреть в экран."""
    logging.exception("ошибка в хендлере", exc_info=event.exception)
    if isinstance(event.exception, TelegramForbiddenError):
        return True
    message = getattr(event.update, "message", None) or getattr(
        getattr(event.update, "callback_query", None), "message", None
    )
    if isinstance(message, Message):
        with suppress(TelegramAPIError):
            await message.answer("Что-то сломалось на моей стороне. Начни заново: /start")
    return True


async def main():
    if not ADMIN_CHAT_ID:
        raise RuntimeError("Не задан ADMIN_CHAT_ID — лиды будет некуда отправлять")
    bot = Bot(
        os.environ["TELEGRAM_BOT_TOKEN"],
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    await bot.set_my_commands([BotCommand(command="start", description="Пройти квиз заново")])
    try:
        # drop_pending_updates: после простоя Telegram отдаёт всё накопленное, и бот
        # начинает отвечать на вчерашние нажатия. Заодно снимает чужой вебхук — иначе 409.
        await dp.start_polling(bot, drop_pending_updates=True)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
