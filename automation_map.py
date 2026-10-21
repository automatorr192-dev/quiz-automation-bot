import json
import logging
import os
import secrets
from pathlib import Path

from dotenv import load_dotenv
from openai import AsyncOpenAI

from quiz import QUIZ

load_dotenv()

MODELS = [
    m.strip()
    for m in os.environ.get(
        "MODELS",
        "nvidia/nemotron-3-super-120b-a12b:free,nvidia/nemotron-3-ultra-550b-a55b:free,deepseek/deepseek-v4-flash",
    ).split(",")
    if m.strip()
]

# Промпт и ссылки на демо лежат в content/: их же читает версия бота на Cloudflare, а эвалы
# проверяют ровно тот текст, что работает в бою.
CONTENT = Path(__file__).parent / "content"
SYSTEM = (CONTENT / "map-prompt.txt").read_text("utf-8")

_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=os.environ["OPENROUTER_API_KEY"],
        )
    return _client


# Под главную боль — живое демо из портфолио. Ставит его код, а не модель: модель могла бы
# выдумать ссылку, а карта без примера — это совет, после которого непонятно, что заказывать.
_demos = json.loads((CONTENT / "demos.json").read_text("utf-8"))
DEMOS = {pain: tuple(demo) for pain, demo in _demos["demos"].items()}
FALLBACK = tuple(_demos["fallback"])


def with_demo(text: str, answers: dict) -> str:
    title, url = DEMOS.get(answers.get("pain"), FALLBACK)
    return f"{text}\n\n👀 Как это выглядит вживую: {title}\n{url}"


def format_answers(answers: dict) -> str:
    return "\n".join(f"{item['q']} {answers.get(item['key'], '—')}" for item in QUIZ)


def fenced(text: str) -> str:
    """Метка случайная: закрыть блок изнутри и дописать свои инструкции снаружи не выйдет."""
    tag = secrets.token_hex(8)
    return f"<<DATA:{tag}>>\n{text}\n<</DATA:{tag}>>"


async def build_map(answers: dict) -> str:
    client = _get_client()
    prompt = "Ответы человека:\n" + fenced(format_answers(answers))
    last_error = "нет ответа"
    for model in MODELS:
        try:
            resp = await client.chat.completions.create(
                model=model,
                max_tokens=4000,
                timeout=90,
                messages=[
                    {"role": "system", "content": SYSTEM},
                    {"role": "user", "content": prompt},
                ],
            )
            content = resp.choices[0].message.content
            if content:
                usage = resp.usage
                logging.info(
                    "карта собрана: %s, %s→%s токенов",
                    model,
                    usage.prompt_tokens if usage else "?",
                    usage.completion_tokens if usage else "?",
                )
                return content
        except Exception as e:
            last_error = str(e)
            logging.warning("модель %s не ответила: %s", model, e)
    # Текст ошибки — в лог, а не человеку: в нём внутренности API и id аккаунта OpenRouter.
    logging.error("карта не собрана ни одной моделью: %s", last_error)
    return "ИИ временно недоступен. Попробуй ещё раз чуть позже: /start"
