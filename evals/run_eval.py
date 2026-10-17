"""Эвалы карты автоматизаций.

    python evals/run_eval.py

Правильного ответа у карты нет, но качество измеримо. Две половины:

- механика — то, что ловится кодом: длина, нет markdown, три раздела карты, нет выдуманных
  сумм и дохода и призыва «закажи» (его добавляет бот, а не модель);
- рубрика — то, что ловится только чтением: опирается ли карта на ответы именно этого
  человека, конкретны ли шаги, нет ли обещаний золотых гор. Читает вторая модель
  (LLM-as-judge) и ставит 1-5 по каждому пункту; проход — не ниже 4 везде.

Судья — тоже модель и тоже ошибается, поэтому его оценка хранится с объяснением: спорные
случаи смотрит человек.
"""

import asyncio
import json
import re
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))

import automation_map  # noqa: E402

RUNS = HERE / "runs"
JUDGE_MODEL = "deepseek/deepseek-v4-flash"
HEADERS = ("🔍", "⚙️", "🚀")
MONEY = re.compile(r"(доход|выручк|прибыл)[^.\n]{0,40}\d|\d[\d\s]*(₽|руб)", re.IGNORECASE)
CTA = re.compile(
    r"(напиши|пиши|обращайся)\s+(мне|нам)|закажи|оставь заявку|свяжись\s+с(о мной| нами)",
    re.IGNORECASE,
)

JUDGE = """Ты проверяешь «карту автоматизаций», которую ИИ написал владельцу малого бизнеса по
его ответам в квизе. Оцени по шкале 1-5:
- grounded: карта опирается на ответы именно этого человека — сферу, размер команды, главную
  боль, цель. 5 — каждая рекомендация вытекает из ответов; 1 — общий текст для кого угодно.
- concrete: шаги конкретные — что автоматизировать, чем и с чего начать на этой неделе.
  5 — можно начать завтра; 1 — «внедрите ИИ».
- honest: нет обещаний золотых гор и выдуманных цифр; оценки экономии времени скромные.
  5 — всё правдоподобно; 1 — «удвоите выручку».
Верни ТОЛЬКО JSON: {"grounded": n, "concrete": n, "honest": n, "why": "одна фраза"}."""


def mechanics(text: str) -> dict:
    return {
        "до 1500 знаков": len(text) <= 1600,
        "без markdown": not any(mark in text for mark in ("**", "##", "`")),
        "три раздела": all(header in text for header in HEADERS),
        "без сумм и дохода": not MONEY.search(text),
        "без призыва": not CTA.search(text),
    }


async def judge(answers: dict, text: str) -> dict:
    client = automation_map._get_client()
    response = await client.chat.completions.create(
        model=JUDGE_MODEL,
        max_tokens=1000,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": JUDGE},
            {
                "role": "user",
                "content": f"Ответы:\n{automation_map.format_answers(answers)}\n\nКарта:\n{text}",
            },
        ],
    )
    return json.loads(response.choices[0].message.content)


async def run_case(case: dict) -> dict:
    text = await automation_map.build_map(case["answers"])
    checks = mechanics(text)
    try:
        grade = await judge(case["answers"], text)
        for key in ("grounded", "concrete", "honest"):
            checks[f"судья: {key}"] = int(grade.get(key, 0)) >= 4
    except Exception as e:
        grade = {"error": repr(e)}
        checks["судья ответил"] = False
    return {"id": case["id"], "checks": checks, "grade": grade, "map": text}


async def main() -> None:
    cases = [json.loads(line) for line in (HERE / "cases.jsonl").read_text("utf-8").splitlines()]
    gate = asyncio.Semaphore(4)

    async def limited(case):
        async with gate:
            return await run_case(case)

    results = await asyncio.gather(*(limited(c) for c in cases))
    for r in results:
        failed = [k for k, v in r["checks"].items() if not v]
        print(f"{'OK  ' if not failed else 'FAIL'} {r['id']:<26} {', '.join(failed)}")

    passed = sum(all(r["checks"].values()) for r in results)
    print(f"\nКарт прошло: {passed} из {len(results)} ({passed / len(results) * 100:.0f}%)")
    for key in ("grounded", "concrete", "honest"):
        marks = [r["grade"][key] for r in results if key in r["grade"]]
        if marks:
            print(f"Судья, {key}: в среднем {sum(marks) / len(marks):.1f} из 5")
    RUNS.mkdir(exist_ok=True)
    path = RUNS / f"{datetime.now():%Y-%m-%d_%H-%M-%S}.json"
    path.write_text(json.dumps(results, ensure_ascii=False, indent=2), "utf-8")
    print(f"Сохранено: {path.relative_to(HERE.parent)}")


if __name__ == "__main__":
    asyncio.run(main())
