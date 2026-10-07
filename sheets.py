import logging
import os
from datetime import datetime, timedelta, timezone

import httpx

from quiz import QUIZ

URL = os.environ.get("SHEETS_WEBHOOK_URL", "").strip()
TOKEN = os.environ.get("SHEETS_TOKEN", "").strip()
MSK = timezone(timedelta(hours=3))
TRANSPORT: httpx.AsyncBaseTransport | None = None


def enabled() -> bool:
    return bool(URL)


def row(lead: dict) -> dict:
    username = lead.get("username")
    values = {
        "Дата": datetime.fromtimestamp(lead["at"], MSK).strftime("%d.%m.%Y %H:%M"),
        "Канал": "MAX" if lead.get("channel") == "max" else "Telegram",
        "Имя": lead.get("name") or "",
        "Ник": f"@{username}" if username else "",
        "Телефон": lead.get("phone") or "",
        "Источник": lead.get("source") or "",
    }
    for item in QUIZ:
        values[item["q"]] = lead["answers"].get(item["key"], "")
    return values


async def send(lead: dict) -> bool:
    if not enabled():
        return False
    try:
        async with httpx.AsyncClient(
            timeout=15, follow_redirects=True, transport=TRANSPORT
        ) as client:
            response = await client.post(URL, json={"token": TOKEN, "row": row(lead)})
        return response.status_code == 200 and response.text.strip().startswith("ok")
    except httpx.HTTPError:
        logging.exception("строка не ушла в Google Sheets")
        return False
