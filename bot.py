import asyncio
import logging
import os
from contextlib import suppress

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BotCommand,
    BufferedInputFile,
    CallbackQuery,
    ErrorEvent,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from aiogram.utils.chat_action import ChatActionSender
from dotenv import load_dotenv

import automation_map
import crm
import export
import funnel
import quiz
import quota
from quiz import QUIZ
from storage import FileStorage

load_dotenv()
logging.basicConfig(level=logging.INFO)

ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID", "")
CONTACT_URL = "https://t.me/aiarchitectorrr"
# Каждая собранная карта — оплаченный вызов модели, а квиз открыт всему интернету.
DAILY_LIMIT = int(os.environ.get("DAILY_LIMIT", 3))
REMIND_AFTER = int(os.environ.get("REMIND_AFTER_MINUTES") or 60) * 60
CRM_WAIT = int(os.environ.get("CRM_WAIT_MINUTES") or 10) * 60
FOLLOW_UP_EVERY = 300
SKIP_PHONE = "Не сейчас"
PHONE_ASK = (
    "Хочешь, разберём карту голосом за 15 минут и прикинем, сколько стоит первый шаг? "
    "Оставь телефон — позвоню в удобное время.\n\n"
    "<i>Нажимая «Оставить телефон», ты соглашаешься, что номер используется только для "
    "этой связи.</i>"
)

dp = Dispatcher(storage=FileStorage())

WELCOME = (
    "👋 Привет! Покажу, <b>что в твоём бизнесе стоит автоматизировать первым</b>.\n\n"
    "7 коротких вопросов (~2 минуты) — и получишь <b>персональную карту автоматизаций</b>: "
    "где утекает время, что автоматизировать и с чего начать.\n\n"
    "Погнали?"
)

# Видно в пустом чате ещё до нажатия «Начать» — единственный шанс объяснить, зачем бот.
DESCRIPTION = (
    "Покажу, что в твоём бизнесе стоит автоматизировать первым.\n\n"
    "7 коротких вопросов — примерно 2 минуты. В ответ получишь персональную карту "
    "автоматизаций: где утекает время, что имеет смысл отдать роботам и с чего начать, "
    "чтобы результат был виден сразу.\n\n"
    "Нажми «Начать»."
)
SHORT_DESCRIPTION = (
    "7 вопросов за 2 минуты — и персональная карта: что в твоём бизнесе автоматизировать первым."
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


def phone_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📞 Оставить телефон", request_contact=True)],
            [KeyboardButton(text=SKIP_PHONE)],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def resume_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="▶ Продолжить", callback_data="resume")]]
    )


def is_admin(message: Message) -> bool:
    return bool(ADMIN_CHAT_ID) and str(message.chat.id) == ADMIN_CHAT_ID


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
    funnel.track(message.from_user.id, "open", command.args or "—")
    await message.answer(WELCOME, reply_markup=start_keyboard())


@dp.callback_query(F.data == "begin")
async def begin(cb: CallbackQuery, state: FSMContext):
    message = chat_message(cb)
    if message is None:
        await restart_hint(cb)
        return
    data = await state.get_data()
    source = data.get("source", "—")
    await state.set_state(Quiz.active)
    await state.set_data({"answers": {}, "source": source})
    funnel.track(cb.from_user.id, "begin", source)
    funnel.progress(cb.from_user.id, message.chat.id, 0, source)
    await show_question(message, state, 0)
    await cb.answer()


@dp.callback_query(F.data == "resume")
async def resume(cb: CallbackQuery, state: FSMContext):
    message = chat_message(cb)
    data = await state.get_data()
    if message is None or await state.get_state() != Quiz.active.state or "step" not in data:
        await restart_hint(cb)
        return
    await cb.answer()
    await show_question(message, state, data["step"])


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
    if step >= len(QUIZ):
        await restart_hint(cb)
        return
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
    funnel.track(cb.from_user.id, "answer", data.get("source"), step)
    if step + 1 < len(QUIZ):
        funnel.progress(cb.from_user.id, message.chat.id, step + 1, data.get("source"))
        await show_question(message, state, step + 1)
    else:
        await finish(cb, message, state)


async def finish(cb: CallbackQuery, message: Message, state: FSMContext):
    data = await state.get_data()
    answers = data["answers"]
    source = data.get("source", "—")
    funnel.done(cb.from_user.id)
    funnel.save_lead(cb.from_user, answers, source)

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
    await message.answer(PHONE_ASK, reply_markup=phone_keyboard())


@dp.message(F.contact)
async def on_contact(message: Message):
    if message.contact.user_id != message.from_user.id:
        await message.answer(
            "Нужен твой собственный номер — кнопкой ниже.", reply_markup=phone_keyboard()
        )
        return
    lead = funnel.set_phone(message.from_user.id, message.contact.phone_number)
    await message.answer(
        "Записал, спасибо! Позвоню в ближайший рабочий день.", reply_markup=ReplyKeyboardRemove()
    )
    if lead is None:
        return
    with suppress(TelegramAPIError):
        await message.bot.send_message(
            ADMIN_CHAT_ID,
            f"📞 Телефон к лиду из квиза: <b>{lead['phone']}</b> — {lead.get('name') or ''}",
        )
    await push(lead)


@dp.message(F.text == SKIP_PHONE)
async def skip_phone(message: Message):
    await message.answer("Хорошо! Карта остаётся выше.", reply_markup=ReplyKeyboardRemove())


@dp.message(Command("stats"))
async def stats(message: Message, command: CommandObject):
    if not is_admin(message):
        return
    days = int(command.args) if command.args and command.args.isdigit() else 30
    await message.answer(stats_text(funnel.report(len(QUIZ), days)))


@dp.message(Command("leads"))
async def leads_file(message: Message, command: CommandObject):
    if not is_admin(message):
        return
    days = int(command.args) if command.args and command.args.isdigit() else 30
    rows = funnel.leads(days)
    if not rows:
        await message.answer(f"За {days} дней лидов нет.")
        return
    data = await asyncio.to_thread(export.leads_xlsx, rows)
    await message.answer_document(
        BufferedInputFile(data, filename=f"quiz-leads-{days}d.xlsx"),
        caption=f"Лиды квиза за {days} дней: {len(rows)}",
    )


@dp.message(Command("quiz"))
async def quiz_file(message: Message):
    if not is_admin(message):
        return
    await message.answer_document(
        BufferedInputFile(quiz.dump(), filename="quiz.json"),
        caption=(
            "Вопросы квиза. Поправь текст, варианты или порядок и пришли файл обратно в этот "
            "чат — проверю и применю сразу. У каждого вопроса нужны key, q и options."
        ),
    )


@dp.message(F.document.file_name.endswith(".json"))
async def quiz_upload(message: Message):
    if not is_admin(message):
        return
    if (message.document.file_size or 0) > 100_000:
        await message.answer("Файл великоват для квиза — до 100 КБ.")
        return
    buffer = await message.bot.download(message.document.file_id)
    try:
        updated = quiz.replace(buffer.read())
    except quiz.BadQuiz as e:
        await message.answer(f"Не применил: {e}. Текущие вопросы остались как были.")
        return
    await message.answer(f"Готово: в квизе {len(updated)} вопросов. Новые проходят уже по ним.")


def _share(part: int, whole: int) -> str:
    return f"{round(part / whole * 100)}%" if whole else "—"


def stats_text(data: dict) -> str:
    started = data["started"]
    lines = [f"📊 <b>Воронка квиза за {data['days']} дней</b>", f"Начали: {started}"]
    for index, count in enumerate(data["answered"], start=1):
        lines.append(f"Ответили на {index}-й: {count} ({_share(count, started)})")
    lines.append(f"Получили карту: {data['finished']} ({_share(data['finished'], started)})")
    lines.append(f"Оставили телефон: {data['phones']} ({_share(data['phones'], data['finished'])})")
    if data["sources"]:
        lines += ["", "<b>Источники</b>"] + [f"{name}: {count}" for name, count in data["sources"]]
    return "\n".join(lines)


async def push(lead: dict) -> None:
    if not crm.enabled():
        return
    if await crm.send(lead) is not None:
        funnel.mark_sent(lead["id"])


async def follow_up(bot: Bot) -> None:
    while True:
        await asyncio.sleep(FOLLOW_UP_EVERY)
        try:
            for session in funnel.stalled(REMIND_AFTER):
                left = len(QUIZ) - session["step"]
                with suppress(TelegramAPIError):
                    await bot.send_message(
                        session["chat_id"],
                        f"Ты остановился на {session['step'] + 1}-м вопросе. "
                        f"Осталось {left} — минута, и карта будет готова.",
                        reply_markup=resume_keyboard(),
                    )
                funnel.reminded(session["user_id"])
            if crm.enabled():
                for lead in funnel.unsent(CRM_WAIT):
                    await push(lead)
        except Exception:
            logging.exception("follow_up упал")


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
    await bot.set_my_description(description=DESCRIPTION)
    await bot.set_my_short_description(short_description=SHORT_DESCRIPTION)
    task = asyncio.create_task(follow_up(bot))
    try:
        # drop_pending_updates: после простоя Telegram отдаёт всё накопленное, и бот
        # начинает отвечать на вчерашние нажатия. Заодно снимает чужой вебхук — иначе 409.
        await dp.start_polling(bot, drop_pending_updates=True)
    finally:
        task.cancel()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
