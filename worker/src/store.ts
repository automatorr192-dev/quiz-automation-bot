// Всё состояние бота в D1: у Worker нет памяти между сообщениями и нет диска.

export type Answers = Record<string, string>;

export interface Session {
  user_id: number;
  chat_id: number;
  step: number;
  answers: Answers;
  source: string | null;
  active: boolean;
  at: number;
  reminded: boolean;
}

export interface Lead {
  id: number;
  user_id: number;
  username: string | null;
  name: string | null;
  phone: string | null;
  source: string | null;
  answers: Answers;
  at: number;
  sent: number;
  sheet: number;
}

export interface Person {
  id: number;
  username?: string;
  first_name: string;
  last_name?: string;
}

export const TARGETS = ["sent", "sheet"] as const;
export type Target = (typeof TARGETS)[number];

type Row = Record<string, any>;

const session = (row: Row): Session => ({
  ...(row as Session),
  answers: JSON.parse(row.answers),
  active: Boolean(row.active),
  reminded: Boolean(row.reminded),
});
const lead = (row: Row): Lead => ({ ...(row as Lead), answers: JSON.parse(row.answers) });

export async function getSession(db: D1Database, userId: number): Promise<Session | null> {
  const row = await db.prepare("select * from sessions where user_id = ?").bind(userId).first();
  return row ? session(row) : null;
}

export async function putSession(db: D1Database, s: Session): Promise<void> {
  await db
    .prepare(
      `insert into sessions (user_id, chat_id, step, answers, source, active, at, reminded)
       values (?, ?, ?, ?, ?, ?, ?, ?)
       on conflict(user_id) do update set chat_id = excluded.chat_id, step = excluded.step,
         answers = excluded.answers, source = excluded.source, active = excluded.active,
         at = excluded.at, reminded = excluded.reminded`,
    )
    .bind(s.user_id, s.chat_id, s.step, JSON.stringify(s.answers), s.source, Number(s.active), s.at, Number(s.reminded))
    .run();
}

export async function dropSession(db: D1Database, userId: number): Promise<void> {
  await db.prepare("delete from sessions where user_id = ?").bind(userId).run();
}

/** Бросившие квиз на середине: напоминаем один раз. */
export async function stalled(db: D1Database, before: number): Promise<Session[]> {
  const { results } = await db
    .prepare("select * from sessions where active = 1 and reminded = 0 and at < ? order by at limit 50")
    .bind(before)
    .all();
  return results.map(session);
}

export async function markReminded(db: D1Database, userId: number): Promise<void> {
  await db.prepare("update sessions set reminded = 1 where user_id = ?").bind(userId).run();
}

export async function track(
  db: D1Database,
  userId: number,
  kind: string,
  source: string | null,
  at: number,
  step: number | null = null,
): Promise<void> {
  await db
    .prepare("insert into events (user_id, kind, step, source, at) values (?, ?, ?, ?, ?)")
    .bind(userId, kind, step, source, at)
    .run();
}

export async function saveLead(
  db: D1Database,
  person: Person,
  answers: Answers,
  source: string | null,
  at: number,
): Promise<number> {
  const name = [person.first_name, person.last_name].filter(Boolean).join(" ");
  const row = await db
    .prepare(
      "insert into leads (user_id, username, name, source, answers, at) values (?, ?, ?, ?, ?, ?) returning id",
    )
    .bind(person.id, person.username ?? null, name, source, JSON.stringify(answers), at)
    .first<{ id: number }>();
  return row!.id;
}

/** Телефон прикрепляется к последнему лиду человека; без пройденного квиза — некуда. */
export async function setPhone(db: D1Database, userId: number, phone: string): Promise<Lead | null> {
  const row = await db
    .prepare("update leads set phone = ? where id = (select max(id) from leads where user_id = ?) returning *")
    .bind(phone, userId)
    .first();
  return row ? lead(row) : null;
}

/** Лиды, которые ещё не ушли в CRM или таблицу: с телефоном — сразу, без — через паузу. */
export async function unsent(db: D1Database, before: number, targets: Target[]): Promise<Lead[]> {
  if (!targets.length) return [];
  const waiting = targets.map((target) => `${target} = 0`).join(" or ");
  const { results } = await db
    .prepare(`select * from leads where (${waiting}) and (phone is not null or at < ?) limit 50`)
    .bind(before)
    .all();
  return results.map(lead);
}

export async function markSent(db: D1Database, id: number, target: Target): Promise<void> {
  if (!TARGETS.includes(target)) throw new Error(target);
  await db.prepare(`update leads set ${target} = 1 where id = ?`).bind(id).run();
}

export async function leads(db: D1Database, since: number): Promise<Lead[]> {
  const { results } = await db
    .prepare("select * from leads where at >= ? order by at desc")
    .bind(since)
    .all();
  return results.map(lead);
}

export interface Report {
  days: number;
  started: number;
  answered: number[];
  finished: number;
  phones: number;
  sources: [string, number][];
}

export async function report(db: D1Database, steps: number, days: number, now: number): Promise<Report> {
  const since = now - days * 86400;
  const count = (sql: string, ...args: unknown[]) => db.prepare(sql).bind(since, ...args);
  const answered = Array.from({ length: steps }, (_, step) =>
    count(
      "select count(distinct user_id) as n from events where at >= ? and kind = 'answer' and step = ?",
      step,
    ),
  );
  const [started, finished, phones, sources, ...reached] = await db.batch<Row>([
    count("select count(distinct user_id) as n from events where at >= ? and kind = 'begin'"),
    count("select count(distinct user_id) as n from leads where at >= ?"),
    count("select count(distinct user_id) as n from leads where at >= ? and phone is not null"),
    count(
      "select coalesce(source, '—') as source, count(*) as n from leads where at >= ? group by source order by n desc limit 8",
    ),
    ...answered,
  ]);
  return {
    days,
    started: started.results[0].n,
    answered: reached.map((result) => result.results[0].n),
    finished: finished.results[0].n,
    phones: phones.results[0].n,
    sources: sources.results.map((row) => [row.source, row.n]),
  };
}

/** Списывает попытку. false — лимит на сутки выбран. */
export async function takeQuota(db: D1Database, userId: number, limit: number, now: number): Promise<boolean> {
  const used = await db
    .prepare("select count(*) as n from quota where user_id = ? and at > ?")
    .bind(userId, now - 86400)
    .first<{ n: number }>();
  if (used!.n >= limit) return false;
  await db.prepare("insert into quota (user_id, at) values (?, ?)").bind(userId, now).run();
  return true;
}

export async function cleanQuota(db: D1Database, now: number): Promise<void> {
  await db.prepare("delete from quota where at <= ?").bind(now - 86400).run();
}

export async function getConfig(db: D1Database, key: string): Promise<string | null> {
  const row = await db.prepare("select value from config where key = ?").bind(key).first<{ value: string }>();
  return row?.value ?? null;
}

export async function setConfig(db: D1Database, key: string, value: string): Promise<void> {
  await db
    .prepare("insert into config (key, value) values (?, ?) on conflict(key) do update set value = excluded.value")
    .bind(key, value)
    .run();
}
