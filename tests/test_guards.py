import pytest

import quota
from automation_map import fenced, format_answers
from quiz import QUIZ


def test_answers_are_fenced_with_random_tag():
    assert fenced("текст") != fenced("текст")
    assert fenced("текст").startswith("<<DATA:")


def test_format_answers_covers_every_question():
    answers = {item["key"]: item["options"][0] for item in QUIZ}
    formatted = format_answers(answers)
    for item in QUIZ:
        assert item["q"] in formatted


def test_quota_stops_after_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(quota, "PATH", str(tmp_path / "quota.json"))
    monkeypatch.setattr(quota, "DATA_DIR", str(tmp_path))
    assert quota.take(42, 2) == 1
    assert quota.take(42, 2) == 0
    with pytest.raises(quota.Exhausted):
        quota.take(42, 2)


def test_quota_survives_restart(tmp_path, monkeypatch):
    monkeypatch.setattr(quota, "PATH", str(tmp_path / "quota.json"))
    monkeypatch.setattr(quota, "DATA_DIR", str(tmp_path))
    quota.take(1, 1)
    with pytest.raises(quota.Exhausted):
        quota.take(1, 1)
