import asyncio
import logging
import os

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.utils.chat_action import ChatActionSender
from dotenv import load_dotenv

import automation_map
from quiz import QUIZ

load_dotenv()
logging.basicConfig(level=logging.INFO)

ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID", "")
CONTACT_URL = "https://t.me/aiarchitectorrr"

dp = Dispatcher()

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
    data = await state.get_data()
    await state.set_state(Quiz.active)
    await state.set_data({"answers": {}, "source": data.get("source", "—")})
    await show_question(cb.message, state, 0)
    await cb.answer()


@dp.callback_query(Quiz.active, F.data == "back")
async def back(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.answer()
    await show_question(cb.message, state, max(0, data["step"] - 1))


@dp.callback_query(Quiz.active, F.data.startswith("ans:"))
async def on_answer(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    step = data["step"]
    answers = data["answers"]
    answers[QUIZ[step]["key"]] = QUIZ[step]["options"][int(cb.data.split(":")[1])]
    await state.update_data(answers=answers)
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.answer()
    if step + 1 < len(QUIZ):
        await show_question(cb.message, state, step + 1)
    else:
        await finish(cb, state)


async def finish(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    answers = data["answers"]
    await cb.message.answer("✨ Собираю твою карту автоматизаций…")
    try:
        async with ChatActionSender.typing(bot=cb.message.bot, chat_id=cb.message.chat.id):
            text = await automation_map.build_map(answers)
    except Exception:
        logging.exception("build_map failed")
        await cb.message.answer(
            "Не получилось собрать карту — ИИ временно недоступен. "
            "Нажми «Пройти заново» чуть позже 🙏",
            reply_markup=finish_keyboard(),
        )
        await notify_lead(cb.message.bot, cb.from_user, answers, data.get("source", "—"))
        await state.clear()
        return
    await cb.message.answer(text, parse_mode=None)
    await cb.message.answer(
        "Понравилось? Такую автоматизацию я могу <b>внедрить под ключ</b> за тебя — "
        "напиши мне, разберём твой случай 👇",
        reply_markup=finish_keyboard(),
    )
    await notify_lead(cb.message.bot, cb.from_user, answers, data.get("source", "—"))
    await state.clear()


async def notify_lead(bot: Bot, user, answers: dict, source: str):
    who = f"@{user.username}" if user.username else f"{user.first_name} (id {user.id})"
    lines = ["🔥 <b>Новый лид из квиза</b>", f"От: {who}", f"Источник: {source}", ""]
    lines += [f"• {item['q']} — <b>{answers.get(item['key'], '—')}</b>" for item in QUIZ]
    await bot.send_message(ADMIN_CHAT_ID, "\n".join(lines))


async def main():
    if not ADMIN_CHAT_ID:
        raise RuntimeError("Не задан ADMIN_CHAT_ID — лиды будет некуда отправлять")
    bot = Bot(
        os.environ["TELEGRAM_BOT_TOKEN"],
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    await bot.set_my_commands([BotCommand(command="start", description="Пройти квиз заново")])
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
