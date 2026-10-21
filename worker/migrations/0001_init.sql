-- Квиз, который человек проходит сейчас. Строка живёт от «Пройти квиз» до карты.
create table sessions (
    user_id integer primary key,
    chat_id integer not null,
    step integer not null default 0,
    answers text not null default '{}',
    source text,
    active integer not null default 0,
    at real not null,
    reminded integer not null default 0
);
create index ix_sessions_stalled on sessions(active, reminded, at);

-- Воронка: открыл, начал, ответил на n-й вопрос.
create table events (
    id integer primary key,
    user_id integer not null,
    kind text not null,
    step integer,
    source text,
    at real not null
);
create index ix_events_at on events(at);

create table leads (
    id integer primary key,
    user_id integer not null,
    username text,
    name text,
    phone text,
    source text,
    answers text not null,
    at real not null,
    sent integer not null default 0,
    sheet integer not null default 0
);
create index ix_leads_user on leads(user_id);

-- Каждая карта — вызов модели, а квиз открыт всему интернету: не больше DAILY_LIMIT в сутки.
create table quota (
    user_id integer not null,
    at real not null
);
create index ix_quota_user on quota(user_id, at);

-- Квиз, присланный владельцем файлом, и отметка, что вебхук установлен.
create table config (
    key text primary key,
    value text not null
);
