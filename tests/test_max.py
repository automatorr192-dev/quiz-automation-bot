import json

import httpx
import pytest

import automation_map
import funnel
import max_bot
import quota
from quiz import QUIZ

USER = {"user_id": 4242, "name": "Анна"}
CHAT = 900


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(funnel, "PATH", str(tmp_path / "quiz.db"))
    monkeypatch.setattr(quota, "PATH", str(tmp_path / "quota.json"))
    monkeypatch.setattr(quota, "DATA_DIR", str(tmp_path))

    async def fake_map(answers):
        return "🔍 Карта готова"

    monkeypatch.setattr(automation_map, "build_map", fake_map)

    sent, answered, notified, pushed = [], [], [], []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/messages":
            sent.append({"chat": request.url.params["chat_id"], **json.loads(request.content)})
        elif request.url.path == "/answers":
            answered.append(request.url.params["callback_id"])
        return httpx.Response(200, json={"success": True})

    client = httpx.AsyncClient(base_url=max_bot.API, transport=httpx.MockTransport(handler))

    async def notify(person, answers, source):
        notified.append((person, answers, source))

    async def push(lead):
        pushed.append(lead)

    bot = max_bot.QuizBot(
        max_bot.Max(client),
        max_bot.States(str(tmp_path / "max_state.json")),
        notify,
        push,
        limit=3,
        contact_url="https://t.me/expert",
    )
    return {"bot": bot, "sent": sent, "answered": answered, "notified": notified, "pushed": pushed}


def press(payload: str, callback_id: str = "cb") -> dict:
    return {
        "update_type": "message_callback",
        "callback": {"callback_id": callback_id, "payload": payload, "user": USER},
        "message": {"recipient": {"chat_id": CHAT}},
    }


async def walk(bot, answers=0):
    await bot.handle({"update_type": "bot_started", "chat_id": CHAT, "user": USER, "payload": "vk"})
    await bot.handle(press("begin"))
    for _ in range(answers):
        await bot.handle(press("ans:0"))


async def test_full_quiz_in_max(world):
    bot = world["bot"]
    await walk(bot, len(QUIZ))

    texts = [m["text"] for m in world["sent"]]
    assert texts[0].startswith("👋")
    assert world["sent"][1]["attachments"][0]["payload"]["buttons"][0][0]["payload"] == "ans:0"
    assert any("Карта готова" in t for t in texts)
    phone_ask = world["sent"][-1]
    assert phone_ask["attachments"][0]["payload"]["buttons"][0][0]["type"] == "request_contact"
    assert world["answered"] and all(a == "cb" for a in world["answered"])

    [lead] = funnel.leads()
    assert lead["channel"] == "max" and lead["source"] == "vk" and lead["user_id"] == -4242
    assert lead["answers"] == {item["key"]: item["options"][0] for item in QUIZ}
    person, _, source = world["notified"][0]
    assert person.first_name == "Анна (MAX)" and source == "vk"
    assert funnel.report(len(QUIZ))["finished"] == 1


async def test_phone_from_max_contact(world):
    bot = world["bot"]
    await walk(bot, len(QUIZ))
    vcf = "BEGIN:VCARD\r\nVERSION:3.0\r\nTEL;TYPE=cell:+7 (999) 111-22-33\r\nEND:VCARD"
    contact = {"type": "contact", "payload": {"vcf_info": vcf, "max_info": {"user_id": 4242}}}
    await bot.handle(
        {
            "update_type": "message_created",
            "message": {
                "sender": USER,
                "recipient": {"chat_id": CHAT},
                "body": {"text": "", "attachments": [contact]},
            },
        }
    )
    assert world["pushed"][0]["phone"] == "+79991112233"


async def test_someone_elses_contact_is_refused(world):
    bot = world["bot"]
    await walk(bot, len(QUIZ))
    vcf = "TEL:+70000000000"
    contact = {"type": "contact", "payload": {"vcf_info": vcf, "max_info": {"user_id": 1}}}
    message = {"sender": USER, "recipient": {"chat_id": CHAT}, "body": {"attachments": [contact]}}
    await bot.handle({"update_type": "message_created", "message": message})
    assert world["pushed"] == []
    assert funnel.leads()[0]["phone"] is None


async def test_back_and_forged_answers(world):
    bot = world["bot"]
    await walk(bot, 2)
    await bot.handle(press("back"))
    assert world["bot"].states.get(4242)["step"] == 1
    before = len(world["sent"])
    await bot.handle(press("ans:99"))
    assert len(world["sent"]) == before


async def test_stalled_max_session_is_reminded_in_max(world):
    bot = world["bot"]
    await walk(bot, 3)
    [session] = funnel.stalled(3600, now=10**10)
    assert session["channel"] == "max" and session["chat_id"] == CHAT
    await bot.remind(session)
    assert world["sent"][-1]["attachments"][0]["payload"]["buttons"][0][0]["payload"] == "resume"


def test_max_ids_never_collide_with_telegram():
    assert max_bot.uid(4242) == -4242
    assert max_bot.uid(-4242) == -4242
