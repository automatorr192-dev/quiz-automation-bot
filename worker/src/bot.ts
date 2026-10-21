import type { CallbackQuery, Message, Update } from "@grammyjs/types";
import profile from "../../content/profile.json";
import { withDemo } from "./map";
import { crmEnabled, leadsCsv, sendCrm, sendSheet, sheetsEnabled } from "./push";
import { BadQuiz, load, parse, progressBar, save, type Quiz } from "./quiz";
import * as store from "./store";
import type { Answers, Lead, Person, Session, Target } from "./store";
import { TelegramError, type Api } from "./telegram";

export interface Deps {
  db: D1Database;
  api: Api;
  env: Env;
  download(fileId: string): Promise<ArrayBuffer>;
  /** Карта собирается дольше, чем Telegram ждёт ответа на вебхук, — её доделывает Workflow. */
  startMap(params: MapParams): Promise<void>;
  now(): number;
}

export interface MapParams {
  chatId: number;
  userId: number;
  answers: Answers;
  source: string;
  who: string;
}

const SKIP_PHONE = "Не сейчас";
const START = { inline_keyboard: [[{ text: "🚀 Пройти квиз", callback_data: "begin" }]] };
const RESUME = { inline_keyboard: [[{ text: "▶ Продолжить", callback_data: "resume" }]] };
const PHONE = {
  keyboard: [[{ text: "📞 Оставить телефон", request_contact: true }], [{ text: SKIP_PHONE }]],
  resize_keyboard: true,
  one_time_keyboard: true,
};
const NO_KEYBOARD = { remove_keyboard: true };

const finishKeyboard = (deps: Deps) => ({
  inline_keyboard: [
    [{ text: "✍️ Написать эксперту", url: deps.env.CONTACT_URL }],
    [{ text: "🔄 Пройти заново", callback_data: "begin" }],
  ],
});

function questionKeyboard(quiz: Quiz, step: number) {
  const rows: { text: string; callback_data: string }[][] = quiz.questions[step].options.map(
    (option, index) => [{ text: option, callback_data: `ans:${index}` }],
  );
  if (step > 0) rows.push([{ text: "⬅ Назад", callback_data: "back" }]);
  return { inline_keyboard: rows };
}

const say = (deps: Deps, chatId: number | string, text: string, extra: Record<string, unknown> = {}) =>
  deps.api("sendMessage", { chat_id: chatId, text, parse_mode: "HTML", ...extra });

const isAdmin = (deps: Deps, message: Message) =>
  Boolean(deps.env.ADMIN_CHAT_ID) && String(message.chat.id) === deps.env.ADMIN_CHAT_ID;

const who = (person: Person) =>
  person.username ? `@${person.username}` : `${person.first_name} (id ${person.id})`;

async function ask(deps: Deps, chatId: number, quiz: Quiz, step: number) {
  const question = quiz.questions[step];
  let text = `${progressBar(step, quiz.questions.length)}\n\n<b>${question.q}</b>`;
  if (question.hint) text += `\n<i>${question.hint}</i>`;
  await say(deps, chatId, text, { reply_markup: questionKeyboard(quiz, step) });
}

export async function handle(update: Update, deps: Deps): Promise<void> {
  if (update.callback_query) return onPress(update.callback_query, deps);
  const message = update.message;
  if (!message?.from) return;
  if (message.contact) return onContact(message, deps);
  if (message.document) return onDocument(message, deps);
  const [command = "", ...rest] = (message.text ?? "").split(" ");
  const arg = rest.join(" ").trim();
  switch (command.split("@")[0]) {
    case "/start":
      return onStart(message, arg, deps);
    case "/stats":
      return onStats(message, arg, deps);
    case "/leads":
      return onLeads(message, arg, deps);
    case "/quiz":
      return onQuizFile(message, deps);
  }
  if (message.text === SKIP_PHONE) {
    await say(deps, message.chat.id, "Хорошо! Карта остаётся выше.", { reply_markup: NO_KEYBOARD });
  }
}

async function onStart(message: Message, arg: string, deps: Deps) {
  const source = arg || "—";
  const user = message.from!;
  await store.putSession(deps.db, {
    user_id: user.id,
    chat_id: message.chat.id,
    step: 0,
    answers: {},
    source,
    active: false,
    at: deps.now(),
    reminded: false,
  });
  await store.track(deps.db, user.id, "open", source, deps.now());
  const quiz = await load(deps.db);
  await say(deps, message.chat.id, quiz.texts.welcome, { reply_markup: START });
}

async function onPress(cb: CallbackQuery, deps: Deps) {
  const answer = (extra: Record<string, unknown> = {}) =>
    deps.api("answerCallbackQuery", { callback_query_id: cb.id, ...extra });
  const restart = () => answer({ text: "Этот квиз уже устарел. Начни заново: /start", show_alert: true });

  // У сообщений старше 48 часов Telegram отдаёт заглушку с date = 0: отвечать в них нельзя.
  const message = cb.message && cb.message.date !== 0 ? cb.message : null;
  const session = await store.getSession(deps.db, cb.from.id);
  const quiz = await load(deps.db);
  const data = cb.data ?? "";

  if (data === "begin") {
    if (!message) return void (await restart());
    const source = session?.source ?? "—";
    await store.putSession(deps.db, {
      user_id: cb.from.id,
      chat_id: message.chat.id,
      step: 0,
      answers: {},
      source,
      active: true,
      at: deps.now(),
      reminded: false,
    });
    await store.track(deps.db, cb.from.id, "begin", source, deps.now());
    await answer();
    return ask(deps, message.chat.id, quiz, 0);
  }

  if (!message || !session?.active || session.step >= quiz.questions.length) {
    return void (await restart());
  }
  const unpress = () =>
    deps
      .api("editMessageReplyMarkup", { chat_id: message.chat.id, message_id: message.message_id })
      .catch(() => {});

  if (data === "resume") {
    await answer();
    return ask(deps, message.chat.id, quiz, session.step);
  }
  if (data === "back") {
    await unpress();
    await answer();
    session.step = Math.max(0, session.step - 1);
    await store.putSession(deps.db, session);
    return ask(deps, message.chat.id, quiz, session.step);
  }
  if (!data.startsWith("ans:")) return void (await answer());

  const options = quiz.questions[session.step].options;
  const choice = data.slice(4);
  // callback_data приходит от клиента, а не из нашей клавиатуры: подделать её можно.
  if (!/^\d+$/.test(choice) || Number(choice) >= options.length) return void (await answer());

  const step = session.step;
  session.answers[quiz.questions[step].key] = options[Number(choice)];
  await unpress();
  await answer();
  await store.track(deps.db, cb.from.id, "answer", session.source, deps.now(), step);
  if (step + 1 < quiz.questions.length) {
    Object.assign(session, { step: step + 1, at: deps.now(), reminded: false });
    await store.putSession(deps.db, session);
    return ask(deps, message.chat.id, quiz, step + 1);
  }
  await finish(cb.from, message.chat.id, session, quiz, deps);
}

async function finish(person: Person, chatId: number, session: Session, quiz: Quiz, deps: Deps) {
  const source = session.source ?? "—";
  await store.dropSession(deps.db, person.id);
  await store.saveLead(deps.db, person, session.answers, source, deps.now());

  const limit = Number(deps.env.DAILY_LIMIT);
  if (!(await store.takeQuota(deps.db, person.id, limit, deps.now()))) {
    await say(
      deps,
      chatId,
      `Карту в сутки собираю ${limit} раза — на сегодня всё. Ответы я записал, приходи завтра 🙏`,
      { reply_markup: finishKeyboard(deps) },
    );
    return notifyLead(deps, quiz, who(person), session.answers, source);
  }
  await say(deps, chatId, "✨ Собираю твою карту автоматизаций…");
  await deps.startMap({ chatId, userId: person.id, answers: session.answers, source, who: who(person) });
}

/** Второй шаг Workflow: карта готова (или модели не ответили) — отдаём человеку и владельцу. */
export async function deliverMap(deps: Deps, params: MapParams, text: string | null): Promise<void> {
  const quiz = await load(deps.db);
  if (text === null) {
    await say(
      deps,
      params.chatId,
      "Не получилось собрать карту — ИИ временно недоступен. Нажми «Пройти заново» чуть позже 🙏",
      { reply_markup: finishKeyboard(deps) },
    );
    return notifyLead(deps, quiz, params.who, params.answers, params.source);
  }
  // Карта — текст модели: без HTML-разметки, иначе случайный «<» в ней уронит отправку.
  await deps.api("sendMessage", { chat_id: params.chatId, text: withDemo(text, params.answers) });
  await say(deps, params.chatId, quiz.texts.finish, { reply_markup: finishKeyboard(deps) });
  await notifyLead(deps, quiz, params.who, params.answers, params.source);
  await say(deps, params.chatId, quiz.texts.phone, { reply_markup: PHONE });
}

/** Лид дороже ответа: если админский чат недоступен, человек не должен об этом узнать,
 * а лид обязан остаться хотя бы в логах. */
async function notifyLead(deps: Deps, quiz: Quiz, from: string, answers: Answers, source: string) {
  const lines = ["🔥 <b>Новый лид из квиза</b>", `От: ${from}`, `Источник: ${source}`, ""];
  lines.push(...quiz.questions.map((item) => `• ${item.q} — <b>${answers[item.key] ?? "—"}</b>`));
  const body = lines.join("\n");
  try {
    await say(deps, deps.env.ADMIN_CHAT_ID, body);
  } catch (error) {
    console.error(`не доставил лид в админский чат: ${error}\n${body}`);
  }
}

async function onContact(message: Message, deps: Deps) {
  const contact = message.contact!;
  if (contact.user_id !== message.from!.id) {
    await say(deps, message.chat.id, "Нужен твой собственный номер — кнопкой ниже.", { reply_markup: PHONE });
    return;
  }
  const lead = await store.setPhone(deps.db, message.from!.id, contact.phone_number);
  const quiz = await load(deps.db);
  await say(deps, message.chat.id, quiz.texts.thanks, { reply_markup: NO_KEYBOARD });
  if (!lead) return;
  await say(deps, deps.env.ADMIN_CHAT_ID, `📞 Телефон к лиду из квиза: <b>${lead.phone}</b> — ${lead.name ?? ""}`).catch(
    () => {},
  );
  await push(deps, quiz, lead);
}

const targets = (deps: Deps): Target[] =>
  [crmEnabled(deps.env) && ("sent" as const), sheetsEnabled(deps.env) && ("sheet" as const)].filter(
    (target): target is Target => Boolean(target),
  );

async function push(deps: Deps, quiz: Quiz, lead: Lead) {
  if (crmEnabled(deps.env) && !lead.sent && (await sendCrm(deps.env, quiz.questions, lead)) !== null) {
    await store.markSent(deps.db, lead.id, "sent");
  }
  if (sheetsEnabled(deps.env) && !lead.sheet && (await sendSheet(deps.env, quiz.questions, lead))) {
    await store.markSent(deps.db, lead.id, "sheet");
  }
}

/** Раз в пять минут: напомнить бросившим квиз и догнать лиды, не ушедшие в CRM и таблицу. */
export async function followUp(deps: Deps): Promise<void> {
  const quiz = await load(deps.db);
  const now = deps.now();
  for (const session of await store.stalled(deps.db, now - Number(deps.env.REMIND_AFTER_MINUTES) * 60)) {
    const left = quiz.questions.length - session.step;
    await say(
      deps,
      session.chat_id,
      `Ты остановился на ${session.step + 1}-м вопросе. Осталось ${left} — минута, и карта будет готова.`,
      { reply_markup: RESUME },
    ).catch(() => {});
    await store.markReminded(deps.db, session.user_id);
  }
  const waiting = targets(deps);
  for (const lead of await store.unsent(deps.db, now - Number(deps.env.CRM_WAIT_MINUTES) * 60, waiting)) {
    await push(deps, quiz, lead);
  }
  await store.cleanQuota(deps.db, now);
}

async function digest(text: string): Promise<string> {
  const hash = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(hash)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

/** Вебхук и профиль бота ставятся из крона: с адреса Worker до Telegram дотягивается
 * сам Worker, а не машина, с которой его деплоят. Повторно — только если сменился адрес,
 * токен или секрет. */
export async function ensureWebhook(deps: Deps): Promise<void> {
  const base = String(deps.env.PUBLIC_URL);
  if (!base) return;
  const url = `${base.replace(/\/$/, "")}/telegram`;
  const mark = `${url}|${await digest(deps.env.TELEGRAM_BOT_TOKEN + deps.env.WEBHOOK_SECRET)}`;
  if ((await store.getConfig(deps.db, "webhook")) === mark) return;
  await deps.api("setWebhook", {
    url,
    secret_token: deps.env.WEBHOOK_SECRET,
    allowed_updates: ["message", "callback_query"],
    drop_pending_updates: true,
  });
  await deps.api("setMyCommands", { commands: [{ command: "start", description: "Пройти квиз заново" }] });
  await deps.api("setMyDescription", { description: profile.description });
  await deps.api("setMyShortDescription", { short_description: profile.short_description });
  await store.setConfig(deps.db, "webhook", mark);
}

const days = (arg: string) => (/^\d+$/.test(arg) ? Number(arg) : 30);
const share = (part: number, whole: number) => (whole ? `${Math.round((part / whole) * 100)}%` : "—");

export function statsText(data: store.Report): string {
  const lines = [`📊 <b>Воронка квиза за ${data.days} дней</b>`, `Начали: ${data.started}`];
  data.answered.forEach((count, index) => {
    lines.push(`Ответили на ${index + 1}-й: ${count} (${share(count, data.started)})`);
  });
  lines.push(`Получили карту: ${data.finished} (${share(data.finished, data.started)})`);
  lines.push(`Оставили телефон: ${data.phones} (${share(data.phones, data.finished)})`);
  if (data.sources.length) {
    lines.push("", "<b>Источники</b>", ...data.sources.map(([name, count]) => `${name}: ${count}`));
  }
  return lines.join("\n");
}

async function onStats(message: Message, arg: string, deps: Deps) {
  if (!isAdmin(deps, message)) return;
  const quiz = await load(deps.db);
  const data = await store.report(deps.db, quiz.questions.length, days(arg), deps.now());
  await say(deps, message.chat.id, statsText(data));
}

async function onLeads(message: Message, arg: string, deps: Deps) {
  if (!isAdmin(deps, message)) return;
  const period = days(arg);
  const rows = await store.leads(deps.db, deps.now() - period * 86400);
  if (!rows.length) return void (await say(deps, message.chat.id, `За ${period} дней лидов нет.`));
  const quiz = await load(deps.db);
  await deps.api("sendDocument", {
    chat_id: message.chat.id,
    document: new File([leadsCsv(quiz.questions, rows)], `quiz-leads-${period}d.csv`),
    caption: `Лиды квиза за ${period} дней: ${rows.length}`,
  });
}

async function onQuizFile(message: Message, deps: Deps) {
  if (!isAdmin(deps, message)) return;
  const quiz = await load(deps.db);
  await deps.api("sendDocument", {
    chat_id: message.chat.id,
    document: new File([JSON.stringify(quiz, null, 2)], "quiz.json"),
    caption:
      "Вопросы и тексты квиза. Поправь формулировки, варианты, порядок или тексты " +
      "приветствия и финала и пришли файл обратно в этот чат — проверю и применю сразу.",
  });
}

async function onDocument(message: Message, deps: Deps) {
  const document = message.document!;
  if (!isAdmin(deps, message) || !document.file_name?.endsWith(".json")) return;
  if ((document.file_size ?? 0) > 100_000) {
    return void (await say(deps, message.chat.id, "Файл великоват для квиза — до 100 КБ."));
  }
  const current = await load(deps.db);
  try {
    // TextDecoder сам срезает BOM, который дописывает Блокнот.
    const raw = new TextDecoder().decode(await deps.download(document.file_id));
    let data: unknown;
    try {
      data = JSON.parse(raw);
    } catch {
      throw new BadQuiz("файл не читается как JSON");
    }
    const quiz = parse(data, current.texts);
    await save(deps.db, quiz);
    await say(
      deps,
      message.chat.id,
      `Готово: в квизе ${quiz.questions.length} вопросов, тексты обновлены. Новые проходят уже по ним.`,
    );
  } catch (error) {
    if (!(error instanceof BadQuiz)) throw error;
    await say(deps, message.chat.id, `Не применил: ${error.message}. Текущие вопросы остались как были.`);
  }
}

/** Исключение в обработчике не должно оставлять человека молча смотреть в экран. */
export async function safely(update: Update, deps: Deps): Promise<void> {
  try {
    await handle(update, deps);
  } catch (error) {
    console.error(`ошибка в обработчике: ${error}`);
    if (error instanceof TelegramError && error.code === 403) return;
    const chatId = update.message?.chat.id ?? update.callback_query?.message?.chat.id;
    if (chatId) await say(deps, chatId, "Что-то сломалось на моей стороне. Начни заново: /start").catch(() => {});
  }
}
