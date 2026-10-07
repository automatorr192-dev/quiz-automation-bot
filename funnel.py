import json
import os
import sqlite3
import time
from contextlib import closing

DATA_DIR = os.environ.get("DATA_DIR") or ("/data" if os.path.isdir("/data") else "data")
PATH = os.path.join(DATA_DIR, "quiz.db")

SCHEMA = """
create table if not exists events (
    id integer primary key,
    user_id integer not null,
    kind text not null,
    step integer,
    source text,
    at real not null
);
create index if not exists ix_events_at on events(at);
create table if not exists leads (
    id integer primary key,
    user_id integer not null,
    username text,
    name text,
    phone text,
    source text,
    answers text not null,
    at real not null,
    sent integer not null default 0
);
create index if not exists ix_leads_user on leads(user_id);
create table if not exists sessions (
    user_id integer primary key,
    chat_id integer not null,
    step integer not null,
    source text,
    at real not null,
    reminded integer not null default 0
);
"""


def _connect() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(PATH) or ".", exist_ok=True)
    connection = sqlite3.connect(PATH)
    connection.row_factory = sqlite3.Row
    connection.executescript(SCHEMA)
    return connection


def track(user_id: int, kind: str, source: str | None = None, step: int | None = None) -> None:
    with closing(_connect()) as db, db:
        db.execute(
            "insert into events (user_id, kind, step, source, at) values (?, ?, ?, ?, ?)",
            (user_id, kind, step, source, time.time()),
        )


def progress(user_id: int, chat_id: int, step: int, source: str | None) -> None:
    with closing(_connect()) as db, db:
        db.execute(
            "insert into sessions (user_id, chat_id, step, source, at, reminded)"
            " values (?, ?, ?, ?, ?, 0)"
            " on conflict(user_id) do update set chat_id = excluded.chat_id,"
            " step = excluded.step, source = excluded.source, at = excluded.at, reminded = 0",
            (user_id, chat_id, step, source, time.time()),
        )


def done(user_id: int) -> None:
    with closing(_connect()) as db, db:
        db.execute("delete from sessions where user_id = ?", (user_id,))


def stalled(after_seconds: int, now: float | None = None) -> list[dict]:
    moment = (now or time.time()) - after_seconds
    with closing(_connect()) as db:
        rows = db.execute(
            "select * from sessions where reminded = 0 and at < ? order by at limit 50", (moment,)
        ).fetchall()
    return [dict(row) for row in rows]


def reminded(user_id: int) -> None:
    with closing(_connect()) as db, db:
        db.execute("update sessions set reminded = 1 where user_id = ?", (user_id,))


def save_lead(user, answers: dict, source: str | None) -> int:
    with closing(_connect()) as db, db:
        cursor = db.execute(
            "insert into leads (user_id, username, name, source, answers, at)"
            " values (?, ?, ?, ?, ?, ?)",
            (
                user.id,
                user.username,
                user.full_name,
                source,
                json.dumps(answers, ensure_ascii=False),
                time.time(),
            ),
        )
        return cursor.lastrowid


def set_phone(user_id: int, phone: str) -> dict | None:
    with closing(_connect()) as db, db:
        row = db.execute(
            "select * from leads where user_id = ? order by id desc limit 1", (user_id,)
        ).fetchone()
        if row is None:
            return None
        db.execute("update leads set phone = ? where id = ?", (phone, row["id"]))
    return {**dict(row), "phone": phone, "answers": json.loads(row["answers"])}


def unsent(older_than: int, now: float | None = None) -> list[dict]:
    moment = (now or time.time()) - older_than
    with closing(_connect()) as db:
        rows = db.execute(
            "select * from leads where sent = 0 and (phone is not null or at < ?) limit 50",
            (moment,),
        ).fetchall()
    return [{**dict(row), "answers": json.loads(row["answers"])} for row in rows]


def mark_sent(lead_id: int) -> None:
    with closing(_connect()) as db, db:
        db.execute("update leads set sent = 1 where id = ?", (lead_id,))


def leads(days: int = 30) -> list[dict]:
    since = time.time() - days * 86400
    with closing(_connect()) as db:
        rows = db.execute("select * from leads where at >= ? order by at desc", (since,)).fetchall()
    return [{**dict(row), "answers": json.loads(row["answers"])} for row in rows]


def report(steps: int, days: int = 30) -> dict:
    since = time.time() - days * 86400
    with closing(_connect()) as db:

        def users(sql: str, *args) -> int:
            return db.execute(sql, (since, *args)).fetchone()[0]

        started = users(
            "select count(distinct user_id) from events where at >= ? and kind = 'begin'"
        )
        reached = [
            users(
                "select count(distinct user_id) from events"
                " where at >= ? and kind = 'answer' and step = ?",
                step,
            )
            for step in range(steps)
        ]
        finished = users("select count(distinct user_id) from leads where at >= ?")
        phones = users(
            "select count(distinct user_id) from leads where at >= ? and phone is not null"
        )
        sources = db.execute(
            "select coalesce(source, '—') as source, count(*) as n from leads where at >= ?"
            " group by source order by n desc limit 8",
            (since,),
        ).fetchall()
    return {
        "days": days,
        "started": started,
        "answered": reached,
        "finished": finished,
        "phones": phones,
        "sources": [(row["source"], row["n"]) for row in sources],
    }
