import json
import os
import re

DATA_DIR = os.environ.get("DATA_DIR") or ("/data" if os.path.isdir("/data") else "data")
PATH = os.path.join(DATA_DIR, "quiz.json")
MAX_QUESTIONS = 12
MAX_OPTIONS = 8


class BadQuiz(Exception):
    pass


# Вопросы и тексты по умолчанию лежат в content/: их же читает версия бота на Cloudflare.
with open(
    os.path.join(os.path.dirname(__file__), "content", "quiz.json"), encoding="utf-8"
) as _file:
    _content = json.load(_file)
DEFAULT, DEFAULT_TEXTS = _content["questions"], _content["texts"]


TAGS = {"b", "i", "u", "s", "a", "code", "pre", "blockquote", "tg-spoiler"}
TAG = re.compile(r"<(/?)([a-z-]+)(?:\s[^<>]*)?>")


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


def html_ok(text: str) -> bool:
    stack = []
    for closing, name in TAG.findall(text):
        if name not in TAGS:
            return False
        if closing:
            if not stack or stack.pop() != name:
                return False
        else:
            stack.append(name)
    return not stack and text.count("<") == len(TAG.findall(text))


def validate_texts(data) -> dict:
    if not isinstance(data, dict):
        raise BadQuiz("texts — объект с текстами бота")
    clean = dict(DEFAULT_TEXTS)
    for key, value in data.items():
        if key not in DEFAULT_TEXTS:
            raise BadQuiz(f"неизвестный текст «{key}», есть: {', '.join(DEFAULT_TEXTS)}")
        if not isinstance(value, str) or not 1 <= len(value.strip()) <= 1500:
            raise BadQuiz(f"текст «{key}» от 1 до 1500 знаков")
        if not html_ok(value):
            raise BadQuiz(
                f"в тексте «{key}» сломана разметка: теги b, i, u, s, a, code должны "
                "закрываться, а знак < писать как &lt;"
            )
        clean[key] = value.strip()
    return clean


def parse(data) -> tuple[list[dict], dict]:
    if isinstance(data, list):
        return validate(data), dict(TEXTS)
    if isinstance(data, dict) and "questions" in data:
        return validate(data["questions"]), validate_texts(data.get("texts") or {})
    raise BadQuiz('ожидается {"questions": [...], "texts": {...}} или список вопросов')


def _stored() -> tuple[list[dict], dict]:
    try:
        with open(PATH, encoding="utf-8") as file:
            data = json.load(file)
        if isinstance(data, list):
            return validate(data), dict(DEFAULT_TEXTS)
        return validate(data["questions"]), validate_texts(data.get("texts") or {})
    except (OSError, ValueError, KeyError, TypeError, BadQuiz):
        return [dict(item) for item in DEFAULT], dict(DEFAULT_TEXTS)


QUIZ, TEXTS = _stored()


def replace(raw: bytes) -> list[dict]:
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError) as e:
        raise BadQuiz("файл не читается как JSON") from e
    questions, texts = parse(data)
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as file:
        json.dump({"questions": questions, "texts": texts}, file, ensure_ascii=False, indent=2)
    os.replace(tmp, PATH)
    QUIZ[:] = questions
    TEXTS.clear()
    TEXTS.update(texts)
    return questions


def dump() -> bytes:
    return json.dumps({"questions": QUIZ, "texts": TEXTS}, ensure_ascii=False, indent=2).encode()
