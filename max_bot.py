import asyncio
import json
import logging
import os
import re
import ssl
from types import SimpleNamespace

import certifi
import httpx

import automation_map
import funnel
import quiz
import quota
from quiz import QUIZ

API = "https://platform-api2.max.ru"
TOKEN = os.environ.get("MAX_BOT_TOKEN", "").strip()
CA = os.path.join(os.path.dirname(__file__), "certs", "russian_trusted_root_ca.pem")
STATE = os.path.join(funnel.DATA_DIR, "max_state.json")
PHONE = re.compile(r"TEL[^:\n]*:([+\d][\d\s()-]{6,})")
TRANSPORT: httpx.AsyncBaseTransport | None = None


def enabled() -> bool:
    return bool(TOKEN)


def uid(user_id: int) -> int:
    return -abs(int(user_id))


def _trust() -> ssl.SSLContext:
    context = ssl.create_default_context(cafile=certifi.where())
    context.load_verify_locations(CA)
    return context


def callback(text: str, payload: str) -> dict:
    return {"type": "callback", "text": text[:128], "payload": payload}


def question_buttons(step: int) -> list[list[dict]]:
    rows = [[callback(option, f"ans:{i}")] for i, option in enumerate(QUIZ[step]["options"])]
    if step > 0:
        rows.append([callback("⬅ Назад", "back")])
    return rows


class States:
    def __init__(self, path: str = STATE):
        self.path = path
        try:
            with open(path, encoding="utf-8") as file:
                self.data = json.load(file)
        except (OSError, ValueError):
            self.data = {}

    def get(self, user_id: int) -> dict:
        return self.data.get(str(user_id), {})

    def put(self, user_id: int, value: dict | None) -> None:
        if value is None:
            self.data.pop(str(user_id), None)
        else:
            self.data[str(user_id)] = value
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as file:
            json.dump(self.data, file, ensure_ascii=False)
        os.replace(tmp, self.path)


class Max:
    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    async def send(
        self, chat_id: int, text: str, buttons: list | None = None, html: bool = True
    ) -> None:
        body: dict = {"text": text[:4000]}
        if html:
            body["format"] = "html"
        if buttons:
            body["attachments"] = [{"type": "inline_keyboard", "payload": {"buttons": buttons}}]
        response = await self.client.post("/messages", params={"chat_id": chat_id}, json=body)
        if response.status_code != 200:
            logging.warning("MAX не принял сообщение: %s %s", response.status_code, response.text)

    async def answer(self, callback_id: str) -> None:
        await self.client.post("/answers", params={"callback_id": callback_id}, json={})

    async def updates(self, marker: int | None) -> tuple[list[dict], int | None]:
        params: dict = {"timeout": 30, "types": "bot_started,message_created,message_callback"}
        if marker is not None:
            params["marker"] = marker
        response = await self.client.get("/updates", params=params)
        response.raise_for_status()
        data = response.json()
        return data.get("updates", []), data.get("marker", marker)


class QuizBot:
    def __init__(self, api: Max, states: States, notify, push, limit: int, contact_url: str):
        self.api, self.states = api, states
        self.notify, self.push = notify, push
        self.limit, self.contact_url = limit, contact_url

    async def handle(self, update: dict) -> None:
        kind = update.get("update_type")
        if kind == "bot_started":
            user = update.get("user") or {}
            await self.welcome(update["chat_id"], user, update.get("payload"))
        elif kind == "message_created":
            message = update.get("message") or {}
            sender = message.get("sender") or {}
            chat_id = (message.get("recipient") or {}).get("chat_id")
            body = message.get("body") or {}
            if (body.get("text") or "").strip().startswith("/start"):
                await self.welcome(chat_id, sender, None)
            else:
                await self.contact(chat_id, sender, body.get("attachments") or [])
        elif kind == "message_callback":
            await self.press(update)

    async def welcome(self, chat_id: int, user: dict, payload: str | None) -> None:
        source = (payload or "—")[:64]
        state = {"source": source, "chat_id": chat_id, "name": user.get("name")}
        self.states.put(user["user_id"], state)
        funnel.track(uid(user["user_id"]), "open", source)
        await self.api.send(chat_id, quiz.TEXTS["welcome"], [[callback("🚀 Пройти квиз", "begin")]])

    async def ask(self, chat_id: int, step: int) -> None:
        item = QUIZ[step]
        bar = "▰" * (step + 1) + "▱" * (len(QUIZ) - step - 1) + f"  {step + 1}/{len(QUIZ)}"
        text = f"{bar}\n\n<b>{item['q']}</b>"
        if item.get("hint"):
            text += f"\n<i>{item['hint']}</i>"
        await self.api.send(chat_id, text, question_buttons(step))

    async def press(self, update: dict) -> None:
        call = update.get("callback") or {}
        user = call.get("user") or {}
        chat_id = ((update.get("message") or {}).get("recipient") or {}).get("chat_id")
        await self.api.answer(call.get("callback_id", ""))
        payload = call.get("payload") or ""
        state = self.states.get(user.get("user_id"))
        if payload == "begin":
            state = {**state, "answers": {}, "step": 0, "chat_id": chat_id}
            state.setdefault("source", "—")
            self.states.put(user["user_id"], state)
            funnel.track(uid(user["user_id"]), "begin", state["source"])
            funnel.progress(uid(user["user_id"]), chat_id, 0, state["source"], "max")
            await self.ask(chat_id, 0)
            return
        if "step" not in state or state["step"] >= len(QUIZ):
            restart = [[callback("🚀 Пройти квиз", "begin")]]
            await self.api.send(chat_id, "Этот квиз устарел. Начнём заново?", restart)
            return
        step = state["step"]
        if payload == "back":
            state["step"] = max(0, step - 1)
            self.states.put(user["user_id"], state)
            await self.ask(chat_id, state["step"])
        elif payload == "resume":
            await self.ask(chat_id, step)
        elif payload.startswith("ans:"):
            choice = payload[4:]
            options = QUIZ[step]["options"]
            if not choice.isdigit() or int(choice) >= len(options):
                return
            state["answers"][QUIZ[step]["key"]] = options[int(choice)]
            funnel.track(uid(user["user_id"]), "answer", state["source"], step)
            if step + 1 < len(QUIZ):
                state["step"] = step + 1
                self.states.put(user["user_id"], state)
                funnel.progress(uid(user["user_id"]), chat_id, step + 1, state["source"], "max")
                await self.ask(chat_id, step + 1)
            else:
                await self.finish(chat_id, user, state)

    async def finish(self, chat_id: int, user: dict, state: dict) -> None:
        person = SimpleNamespace(
            id=uid(user["user_id"]),
            username=None,
            full_name=user.get("name") or state.get("name"),
            first_name=f"{user.get('name') or 'Без имени'} (MAX)",
        )
        answers, source = state["answers"], state["source"]
        funnel.done(person.id)
        funnel.save_lead(person, answers, source, channel="max")
        self.states.put(user["user_id"], {"source": source, "chat_id": chat_id})
        again = [
            [{"type": "link", "text": "✍️ Написать эксперту", "url": self.contact_url}],
            [callback("🔄 Пройти заново", "begin")],
        ]
        try:
            quota.take(person.id, self.limit, "max")
        except quota.Exhausted as e:
            limit = f"Карту в сутки собираю {e.limit} раза — приходи завтра 🙏"
            await self.api.send(chat_id, limit, again)
            await self.notify(person, answers, source)
            return
        await self.api.send(chat_id, "✨ Собираю твою карту автоматизаций…", html=False)
        try:
            text = await automation_map.build_map(answers)
        except Exception:
            logging.exception("MAX: build_map failed")
            text = "Не получилось собрать карту — ИИ временно недоступен. Попробуй чуть позже."
        await self.api.send(chat_id, automation_map.with_demo(text, answers), html=False)
        await self.api.send(chat_id, quiz.TEXTS["finish"], again)
        await self.notify(person, answers, source)
        await self.api.send(
            chat_id,
            quiz.TEXTS["phone"],
            [[{"type": "request_contact", "text": "📞 Оставить телефон"}]],
        )

    async def contact(self, chat_id: int, sender: dict, attachments: list[dict]) -> None:
        for item in attachments:
            if item.get("type") != "contact":
                continue
            payload = item.get("payload") or {}
            owner = (payload.get("max_info") or {}).get("user_id")
            if owner is not None and owner != sender.get("user_id"):
                await self.api.send(chat_id, "Нужен твой собственный номер — кнопкой выше.")
                return
            match = PHONE.search(payload.get("vcf_info") or "")
            if not match:
                return
            phone = re.sub(r"[^\d+]", "", match.group(1))
            lead = funnel.set_phone(uid(sender["user_id"]), phone)
            await self.api.send(chat_id, quiz.TEXTS["thanks"], html=False)
            if lead is not None:
                await self.push(lead)
            return

    async def remind(self, session: dict) -> None:
        left = len(QUIZ) - session["step"]
        await self.api.send(
            session["chat_id"],
            f"Ты остановился на {session['step'] + 1}-м вопросе. "
            f"Осталось {left} — минута, и карта будет готова.",
            [[callback("▶ Продолжить", "resume")]],
        )


def client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=API,
        headers={"Authorization": TOKEN},
        verify=_trust() if TRANSPORT is None else True,
        transport=TRANSPORT,
        timeout=45,
    )


async def run(bot: QuizBot) -> None:
    marker = None
    while True:
        try:
            updates, marker = await bot.api.updates(marker)
            for update in updates:
                try:
                    await bot.handle(update)
                except Exception:
                    logging.exception("MAX: ошибка в обработке апдейта")
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.exception("MAX: опрос упал, повтор через 5 секунд")
            await asyncio.sleep(5)
