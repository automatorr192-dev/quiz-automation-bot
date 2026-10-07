import hashlib
import hmac
import json
import logging
import os
import time

import httpx

from quiz import QUIZ

URL = os.environ.get("CRM_WEBHOOK_URL", "").strip()
SECRET = os.environ.get("CRM_WEBHOOK_SECRET", "").strip()
TRANSPORT: httpx.AsyncBaseTransport | None = None


def enabled() -> bool:
    return bool(URL and SECRET)


def payload(lead: dict) -> dict:
    answers = lead["answers"]
    lines = ["Квиз «карта автоматизаций»:"]
    lines += [f"• {item['q']} — {answers.get(item['key'], '—')}" for item in QUIZ]
    username = lead.get("username")
    contact = lead.get("phone") or (f"@{username}" if username else f"tg id {lead['user_id']}")
    source = lead.get("source")
    return {
        "text": "\n".join(lines),
        "name": lead.get("name"),
        "contact": contact,
        "source": "квиз-бот",
        "utm_source": source if source and source != "—" else None,
        "consent": bool(lead.get("phone")),
    }


def sign(body: bytes, timestamp: str) -> str:
    return hmac.new(SECRET.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()


async def send(lead: dict) -> int | None:
    if not enabled():
        return None
    body = json.dumps(payload(lead), ensure_ascii=False).encode()
    timestamp = str(int(time.time()))
    headers = {
        "Content-Type": "application/json",
        "X-Timestamp": timestamp,
        "X-Signature": sign(body, timestamp),
    }
    try:
        async with httpx.AsyncClient(timeout=10, transport=TRANSPORT) as client:
            response = await client.post(URL, content=body, headers=headers)
        response.raise_for_status()
        return response.json().get("id")
    except (httpx.HTTPError, ValueError):
        logging.exception("лид не ушёл в CRM")
        return None
