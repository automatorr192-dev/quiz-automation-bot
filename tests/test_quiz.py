from bot import progress_bar
from quiz import QUIZ


def test_quiz_keys_are_unique():
    keys = [item["key"] for item in QUIZ]
    assert len(keys) == len(set(keys))


def test_every_question_is_answerable():
    for item in QUIZ:
        assert item["key"]
        assert item["q"]
        assert len(item["options"]) >= 2


def test_progress_bar_on_first_question():
    assert progress_bar(0, len(QUIZ)) == "▰▱▱▱▱▱▱  1/7"


def test_progress_bar_on_last_question():
    assert progress_bar(len(QUIZ) - 1, len(QUIZ)) == "▰▰▰▰▰▰▰  7/7"
