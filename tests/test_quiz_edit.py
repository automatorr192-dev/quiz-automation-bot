import json

import pytest

import quiz
from bot import QUIZ as BOT_QUIZ


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(quiz, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(quiz, "PATH", str(tmp_path / "quiz.json"))
    saved = [dict(item) for item in quiz.QUIZ]
    yield
    quiz.QUIZ[:] = saved


def question(key: str, options=("Да", "Нет")) -> dict:
    return {"key": key, "q": f"Вопрос {key}?", "options": list(options)}


def test_owner_replaces_questions_without_restart(tmp_path):
    raw = json.dumps([question("a"), question("b", ("Мало", "Средне", "Много"))]).encode()
    updated = quiz.replace(raw)
    assert [item["key"] for item in updated] == ["a", "b"]
    assert BOT_QUIZ is quiz.QUIZ and len(BOT_QUIZ) == 2
    assert (
        json.loads((tmp_path / "quiz.json").read_text(encoding="utf-8"))[1]["options"][2] == "Много"
    )


def test_dump_round_trips():
    assert quiz.validate(json.loads(quiz.dump())) == quiz.QUIZ


@pytest.mark.parametrize(
    ("data", "problem"),
    [
        ("{}", "список"),
        ("[]", "список"),
        (json.dumps([question("a"), question("a")]), "повторяется"),
        (json.dumps([{"key": "a", "q": "Вопрос?", "options": ["один"]}]), "вариантов"),
        (json.dumps([{"key": "a", "q": "", "options": ["1", "2"]}]), "текст вопроса"),
        ("не json", "JSON"),
    ],
)
def test_broken_file_keeps_current_questions(data, problem):
    before = list(quiz.QUIZ)
    with pytest.raises(quiz.BadQuiz, match=problem):
        quiz.replace(data.encode())
    assert quiz.QUIZ == before


def test_file_with_bom_from_notepad_is_accepted():
    raw = "\ufeff".encode() + json.dumps([question("a")]).encode()
    assert quiz.replace(raw)[0]["key"] == "a"
