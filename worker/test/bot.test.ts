import { env } from "cloudflare:test";
import { describe, expect, it } from "vitest";
import { deliverMap, ensureWebhook, followUp, handle } from "../src/bot";
import { DEFAULT } from "../src/quiz";
import * as store from "../src/store";
import { ADMIN, POLINA, contact, document, press, text, world } from "./world";

const QUESTIONS = DEFAULT.questions;

async function passQuiz(w: ReturnType<typeof world>, from = POLINA) {
  await handle(text("/start reels", from), w.deps);
  await handle(press("begin", from), w.deps);
  for (const _ of QUESTIONS) await handle(press("ans:0", from), w.deps);
}

describe("квиз", () => {
  it("целиком: вопросы по порядку, лид сохранён, карта заказана с ответами и источником", async () => {
    const w = world();
    await passQuiz(w);

    const questions = w.sent().filter((m) => m.payload.reply_markup?.inline_keyboard?.[0]?.[0]?.callback_data === "ans:0");
    expect(questions).toHaveLength(QUESTIONS.length);
    expect(questions[0].payload.text).toContain(`1/${QUESTIONS.length}`);
    expect(w.last().text).toContain("Собираю твою карту");

    expect(w.maps).toHaveLength(1);
    expect(w.maps[0].source).toBe("reels");
    expect(w.maps[0].who).toBe("@polina");
    expect(Object.keys(w.maps[0].answers)).toEqual(QUESTIONS.map((q) => q.key));

    expect(await store.getSession(env.DB, POLINA.id)).toBeNull();
    const [lead] = await store.leads(env.DB, 0);
    expect(lead.answers.sphere).toBe(QUESTIONS[0].options[0]);
  });

  it("«Назад» возвращает к прошлому вопросу, поддельный ответ ничего не меняет", async () => {
    const w = world();
    await handle(text("/start"), w.deps);
    await handle(press("begin"), w.deps);
    await handle(press("ans:1"), w.deps);
    await handle(press("ans:99"), w.deps);
    await handle(press("ans:x"), w.deps);
    expect((await store.getSession(env.DB, POLINA.id))!.step).toBe(1);

    await handle(press("back"), w.deps);
    const session = await store.getSession(env.DB, POLINA.id);
    expect(session!.step).toBe(0);
    expect(w.last().text).toContain(`1/${QUESTIONS.length}`);
  });

  it("нажатие в сообщении старше 48 часов просит начать заново", async () => {
    const w = world();
    await handle(press("begin", POLINA, 0), w.deps);
    expect(w.calls.at(-1)).toMatchObject({ method: "answerCallbackQuery", payload: { show_alert: true } });
  });

  it("ответ без начатого квиза не падает, а предлагает начать заново", async () => {
    const w = world();
    await handle(press("ans:0"), w.deps);
    expect(w.calls.at(-1)!.payload.text).toContain("/start");
    expect(w.maps).toHaveLength(0);
  });

  it("после суточного лимита карта не заказывается, но лид доходит до владельца", async () => {
    const w = world({ DAILY_LIMIT: "1" });
    await passQuiz(w);
    await passQuiz(w);

    expect(w.maps).toHaveLength(1);
    expect(w.last().text).toContain("на сегодня всё");
    expect(w.sent(ADMIN).at(-1)!.payload.text).toContain("Новый лид из квиза");
    expect(await store.leads(env.DB, 0)).toHaveLength(2);
  });
});

describe("карта", () => {
  const params = { chatId: POLINA.id, userId: POLINA.id, answers: { pain: "Запись и напоминания" }, source: "—", who: "@polina" };

  it("уходит без HTML с живым демо, потом финал, лид владельцу и просьба телефона", async () => {
    const w = world();
    await deliverMap(w.deps, params, "🔍 Где утекает время — <в записи>");

    const [map, finish, phone] = w.sent();
    expect(map.payload.parse_mode).toBeUndefined();
    expect(map.payload.text).toContain("<в записи>");
    expect(map.payload.text).toContain("/glyanets/");
    expect(finish.payload.reply_markup.inline_keyboard[0][0].url).toBe(env.CONTACT_URL);
    expect(phone.payload.reply_markup.keyboard[0][0].request_contact).toBe(true);
    expect(w.sent(ADMIN)).toHaveLength(1);
  });

  it("если модели не ответили — честное сообщение, а лид всё равно у владельца", async () => {
    const w = world();
    await deliverMap(w.deps, params, null);
    expect(w.last().text).toContain("Не получилось собрать карту");
    expect(w.sent(ADMIN)).toHaveLength(1);
  });

  it("недоступный админский чат не ломает выдачу карты человеку", async () => {
    const w = world();
    const api = w.deps.api;
    w.deps.api = async (method, payload) => {
      if (payload?.chat_id === ADMIN) throw new Error("chat not found");
      return api(method, payload);
    };
    await deliverMap(w.deps, params, "карта");
    expect(w.sent()).toHaveLength(3);
  });
});

describe("телефон", () => {
  it("прикрепляется к последнему лиду и приходит владельцу", async () => {
    const w = world();
    await passQuiz(w);
    await handle(contact("+79991112233", POLINA.id), w.deps);

    const [lead] = await store.leads(env.DB, 0);
    expect(lead.phone).toBe("+79991112233");
    expect(w.sent(ADMIN).at(-1)!.payload.text).toContain("+79991112233");
    expect(w.last().reply_markup).toEqual({ remove_keyboard: true });
  });

  it("чужой контакт не принимается", async () => {
    const w = world();
    await passQuiz(w);
    await handle(contact("+70000000000", 777), w.deps);
    const [lead] = await store.leads(env.DB, 0);
    expect(lead.phone).toBeNull();
    expect(w.last().text).toContain("собственный номер");
  });

  it("без пройденного квиза телефон некуда приложить", async () => {
    const w = world();
    await handle(contact("+79991112233", POLINA.id), w.deps);
    expect(w.sent(ADMIN)).toHaveLength(0);
  });
});

describe("напоминания", () => {
  it("бросившему квиз — одно напоминание с кнопкой «Продолжить»", async () => {
    const w = world();
    await handle(text("/start"), w.deps);
    await handle(press("begin"), w.deps);
    await handle(press("ans:0"), w.deps);

    w.tick(61 * 60);
    await followUp(w.deps);
    await followUp(w.deps);

    const reminders = w.sent().filter((m) => m.payload.text.startsWith("Ты остановился"));
    expect(reminders).toHaveLength(1);
    expect(reminders[0].payload.text).toContain("на 2-м вопросе");

    await handle(press("resume"), w.deps);
    expect(w.last().text).toContain(`2/${QUESTIONS.length}`);
  });

  it("тому, кто только открыл бота, не напоминаем", async () => {
    const w = world();
    await handle(text("/start"), w.deps);
    w.tick(2 * 3600);
    await followUp(w.deps);
    expect(w.sent()).toHaveLength(1);
  });
});

describe("владелец", () => {
  const admin = { ...POLINA, id: Number(ADMIN), username: "owner" };

  it("видит воронку с отвалом по вопросам", async () => {
    const w = world();
    await passQuiz(w);
    await handle(text("/start", { ...POLINA, id: 43 }), w.deps);
    await handle(press("begin", { ...POLINA, id: 43 }), w.deps);
    await handle(press("ans:0", { ...POLINA, id: 43 }), w.deps);

    await handle(text("/stats", admin), w.deps);
    const report = w.last(ADMIN).text;
    expect(report).toContain("Начали: 2");
    expect(report).toContain("Ответили на 1-й: 2 (100%)");
    expect(report).toContain("Ответили на 2-й: 1 (50%)");
    expect(report).toContain("Получили карту: 1 (50%)");
    expect(report).toContain("reels: 1");
  });

  it("чужим людям команды владельца не отвечают", async () => {
    const w = world();
    await handle(text("/stats"), w.deps);
    await handle(text("/leads"), w.deps);
    expect(w.calls).toHaveLength(0);
  });

  it("выгружает лиды файлом с колонкой на каждый вопрос", async () => {
    const w = world();
    await passQuiz(w);
    await handle(text("/leads 7", admin), w.deps);
    const call = w.calls.at(-1)!;
    expect(call.method).toBe("sendDocument");
    const csv = await (call.payload.document as File).text();
    for (const question of QUESTIONS) expect(csv).toContain(question.q);
    expect(csv).toContain("@polina");
  });

  it("меняет вопросы файлом без передеплоя, файл с BOM из Блокнота тоже принимается", async () => {
    const w = world();
    const questions = [{ key: "a", q: "Новый вопрос?", options: ["да", "нет"] }];
    w.files.push(new TextEncoder().encode("﻿" + JSON.stringify({ questions, texts: { welcome: "<b>Привет</b>" } })).buffer as ArrayBuffer);
    await handle(document("quiz.json"), w.deps);
    expect(w.last(ADMIN).text).toContain("в квизе 1 вопросов");

    await handle(text("/start"), w.deps);
    expect(w.last().text).toBe("<b>Привет</b>");
    await handle(press("begin"), w.deps);
    expect(w.last().text).toContain("Новый вопрос?");
  });

  it("сломанный файл не применяется и объясняет, что не так", async () => {
    const w = world();
    w.files.push(new TextEncoder().encode('{"questions": [], "texts": {}}').buffer as ArrayBuffer);
    await handle(document("quiz.json"), w.deps);
    expect(w.last(ADMIN).text).toContain("Не применил: нужен список из 1–12 вопросов");

    w.files.push(new TextEncoder().encode("не json").buffer as ArrayBuffer);
    await handle(document("quiz.json"), w.deps);
    expect(w.last(ADMIN).text).toContain("файл не читается как JSON");

    await handle(text("/start"), w.deps);
    expect(w.last().text).toBe(DEFAULT.texts.welcome);
  });
});

describe("вебхук", () => {
  it("ставится один раз и заново — только при смене адреса", async () => {
    const w = world({ PUBLIC_URL: "https://quiz.example.workers.dev" });
    await ensureWebhook(w.deps);
    await ensureWebhook(w.deps);

    const hooks = w.calls.filter((c) => c.method === "setWebhook");
    expect(hooks).toHaveLength(1);
    expect(hooks[0].payload).toMatchObject({ url: "https://quiz.example.workers.dev/telegram", secret_token: "test-secret" });
    expect(w.calls.map((c) => c.method)).toContain("setMyDescription");

    const moved = world({ PUBLIC_URL: "https://quiz.other.workers.dev/" });
    await ensureWebhook(moved.deps);
    expect(moved.calls[0].payload.url).toBe("https://quiz.other.workers.dev/telegram");
  });

  it("без адреса ничего не делает", async () => {
    const w = world();
    await ensureWebhook(w.deps);
    expect(w.calls).toHaveLength(0);
  });
});
