"""После карты — ссылка на живое демо под главную боль. Ставит её код, а не модель."""

import pytest

import automation_map
from quiz import QUIZ

PAINS = next(q for q in QUIZ if q["key"] == "pain")["options"]


@pytest.mark.parametrize("pain", PAINS)
def test_every_pain_gets_a_live_link(pain):
    text = automation_map.with_demo("Карта", {"pain": pain})

    assert text.startswith("Карта\n\n👀 Как это выглядит вживую: ")
    assert text.splitlines()[-1].startswith("https://")


def test_mapped_pains_are_real_quiz_options():
    assert set(automation_map.DEMOS) <= set(PAINS)


def test_records_go_to_the_booking_demo():
    text = automation_map.with_demo("Карта", {"pain": "Запись и напоминания"})

    assert text.endswith("/glyanets/")
