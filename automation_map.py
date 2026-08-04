import os

from dotenv import load_dotenv
from openai import AsyncOpenAI

from quiz import QUIZ

load_dotenv()

MODELS = [
    m.strip()
    for m in os.environ.get(
        "MODELS",
        "deepseek/deepseek-v4-flash,openai/gpt-oss-120b:free,nvidia/nemotron-3-super-120b-a12b:free",
    ).split(",")
    if m.strip()
]

SYSTEM = (
    "Ты — AI-автоматизатор, который помогает малому бизнесу убирать рутину через ИИ. "
    "На входе — ответы человека из мини-квиза. На выходе — короткая персональная "
    "«карта автоматизаций».\n\n"
    "Тон: спокойный эксперт, конкретика без хайпа, на «ты», по-русски.\n"
    "Опирайся на его сегмент, размер команды, боли и цель — карта должна быть под него.\n"
    "НЕ используй markdown-разметку (никаких **, ##, обратных кавычек). "
    "Для структуры используй эмодзи-заголовки и абзацы, каждый пункт с новой строки.\n\n"
    "Структура ответа:\n"
    "🔍 Где утекает время — одно предложение по его ответам.\n"
    "⚙️ Что автоматизировать — 2-3 приоритета. Для каждого: что автоматизируем, "
    "чем (бот / скрипт / связка сервисов), сколько времени в неделю это вернёт.\n"
    "🚀 С чего начать — один конкретный первый шаг на этой неделе.\n\n"
    "Пиши сжато и живо, без воды и без обещаний золотых гор. Не выдумывай "
    "цифры дохода. Максимум ~1500 знаков. Не добавляй призыв написать/заказать — его добавит бот."
)

_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=os.environ["OPENROUTER_API_KEY"],
        )
    return _client


def format_answers(answers: dict) -> str:
    return "\n".join(f"{item['q']} {answers.get(item['key'], '—')}" for item in QUIZ)


async def build_map(answers: dict) -> str:
    client = _get_client()
    prompt = "Ответы человека:\n" + format_answers(answers)
    last_error = "нет ответа"
    for model in MODELS:
        try:
            resp = await client.chat.completions.create(
                model=model,
                max_tokens=1200,
                messages=[
                    {"role": "system", "content": SYSTEM},
                    {"role": "user", "content": prompt},
                ],
            )
            content = resp.choices[0].message.content
            if content:
                return content
        except Exception as e:
            last_error = str(e)
    return f"ИИ временно недоступен ({last_error}). Попробуй ещё раз: /start"
