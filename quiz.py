import json
import os

DATA_DIR = os.environ.get("DATA_DIR") or ("/data" if os.path.isdir("/data") else "data")
PATH = os.path.join(DATA_DIR, "quiz.json")
MAX_QUESTIONS = 12
MAX_OPTIONS = 8


class BadQuiz(Exception):
    pass


DEFAULT = [
    {
        "key": "sphere",
        "q": "Для начала — какой у тебя бизнес?",
        "hint": "Собираю твою карту автоматизаций",
        "options": [
            "Услуги офлайн (салон, клиника, студия)",
            "Онлайн-школа / эксперт / блог",
            "Интернет-магазин / маркетплейс",
            "Агентство / команда под проекты",
            "Другое",
        ],
    },
    {
        "key": "team",
        "q": "Сколько человек в команде, вместе с тобой?",
        "hint": "Хорошо, профиль вырисовывается",
        "options": [
            "Только я",
            "2-5 человек",
            "6-20 человек",
            "20+ человек",
        ],
    },
    {
        "key": "pain",
        "q": "Что сейчас отнимает больше всего времени?",
        "hint": "Здесь обычно прячутся часы в неделю",
        "options": [
            "Переписка и ответы клиентам",
            "Запись и напоминания",
            "Заявки и работа с лидами",
            "Счета и документы",
            "Контент и соцсети",
            "Отчёты и сбор данных",
        ],
    },
    {
        "key": "current",
        "q": "Как сейчас с этим справляешься?",
        "hint": "Подберу реально подходящее решение",
        "options": [
            "Всё вручную",
            "Excel / Google-таблицы",
            "CRM есть, но работает частично",
            "Уже автоматизировано, хочу лучше",
        ],
    },
    {
        "key": "secondary",
        "q": "Что ещё вечно откладываешь или не успеваешь?",
        "hint": "Карта складывается из нескольких слоёв",
        "options": [
            "Постинг и продвижение",
            "Повторные касания с клиентами",
            "Финансы и налоги",
            "Найм и обучение людей",
            "Ничего, всё под контролем",
        ],
    },
    {
        "key": "urgency",
        "q": "Насколько это мешает росту прямо сейчас?",
        "hint": "Почти готово",
        "options": [
            "Жжёт — теряю клиентов и деньги",
            "Мешает, но терпимо",
            "Хочу разобраться, не срочно",
            "Просто интересно, что есть",
        ],
    },
    {
        "key": "goal",
        "q": "Что для тебя главное в автоматизации?",
        "hint": "Последний вопрос перед картой",
        "options": [
            "Сэкономить своё время",
            "Не терять клиентов и заявки",
            "Выглядеть профессионально",
            "Расти без найма новых людей",
        ],
    },
]


def validate(data) -> list[dict]:
    if not isinstance(data, list) or not 1 <= len(data) <= MAX_QUESTIONS:
        raise BadQuiz(f"нужен список из 1–{MAX_QUESTIONS} вопросов")
    keys = set()
    clean = []
    for number, item in enumerate(data, start=1):
        if not isinstance(item, dict):
            raise BadQuiz(f"вопрос {number}: ожидается объект с полями key, q, options")
        key, question, options = item.get("key"), item.get("q"), item.get("options")
        if not isinstance(key, str) or not key.strip() or key in keys:
            raise BadQuiz(f"вопрос {number}: key пустой или повторяется")
        if not isinstance(question, str) or not 3 <= len(question.strip()) <= 300:
            raise BadQuiz(f"вопрос {number}: текст вопроса от 3 до 300 знаков")
        if (
            not isinstance(options, list)
            or not 2 <= len(options) <= MAX_OPTIONS
            or not all(isinstance(o, str) and 1 <= len(o.strip()) <= 60 for o in options)
        ):
            raise BadQuiz(f"вопрос {number}: от 2 до {MAX_OPTIONS} вариантов по 1–60 знаков")
        hint = item.get("hint")
        if hint is not None and (not isinstance(hint, str) or len(hint) > 200):
            raise BadQuiz(f"вопрос {number}: подсказка до 200 знаков")
        keys.add(key)
        entry = {"key": key.strip(), "q": question.strip(), "options": [o.strip() for o in options]}
        if hint:
            entry["hint"] = hint.strip()
        clean.append(entry)
    return clean


def _stored() -> list[dict]:
    try:
        with open(PATH, encoding="utf-8") as file:
            return validate(json.load(file))
    except (OSError, ValueError, BadQuiz):
        return [dict(item) for item in DEFAULT]


QUIZ = _stored()


def replace(raw: bytes) -> list[dict]:
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError) as e:
        raise BadQuiz("файл не читается как JSON") from e
    clean = validate(data)
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as file:
        json.dump(clean, file, ensure_ascii=False, indent=2)
    os.replace(tmp, PATH)
    QUIZ[:] = clean
    return clean


def dump() -> bytes:
    return json.dumps(QUIZ, ensure_ascii=False, indent=2).encode()
