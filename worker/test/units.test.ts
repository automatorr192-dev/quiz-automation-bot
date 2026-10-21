import { env } from "cloudflare:test";
import { afterEach, describe, expect, it, vi } from "vitest";
import worker from "../src/index";
import { buildMap, fenced, formatAnswers, SYSTEM, withDemo } from "../src/map";
import { crmPayload, sendCrm, sendSheet, sign } from "../src/push";
import { BadQuiz, DEFAULT, htmlOk, parse, progressBar, validate } from "../src/quiz";
import type { Lead } from "../src/store";

afterEach(() => vi.restoreAllMocks());

describe("квиз по умолчанию", () => {
  it("ключи уникальны, на каждый вопрос можно ответить", () => {
    const keys = DEFAULT.questions.map((q) => q.key);
    expect(new Set(keys).size).toBe(keys.length);
    for (const q of DEFAULT.questions) expect(q.options.length).toBeGreaterThanOrEqual(2);
  });

  it("полоса прогресса на первом и последнем вопросе", () => {
    expect(progressBar(0, 7)).toBe("▰▱▱▱▱▱▱  1/7");
    expect(progressBar(6, 7)).toBe("▰▰▰▰▰▰▰  7/7");
  });
});

describe("файл квиза от владельца", () => {
  it("сломанная разметка в текстах отклоняется", () => {
    expect(htmlOk("<b>жирный</b> и <i>курсив</i>")).toBe(true);
    expect(htmlOk("<b>не закрыт")).toBe(false);
    expect(htmlOk("<script>alert(1)</script>")).toBe(false);
    expect(htmlOk("цена < 100")).toBe(false);
    expect(() => parse({ questions: DEFAULT.questions, texts: { welcome: "<b>" } }, DEFAULT.texts)).toThrow(
      /сломана разметка/,
    );
  });

  it("неизвестный текст называется по имени", () => {
    expect(() => parse({ questions: DEFAULT.questions, texts: { bye: "пока" } }, DEFAULT.texts)).toThrow(
      /неизвестный текст «bye»/,
    );
  });

  it("старый формат — просто список вопросов — работает и сохраняет текущие тексты", () => {
    const quiz = parse([{ key: "a", q: "Вопрос?", options: ["1", "2"] }], { ...DEFAULT.texts, thanks: "мерси" });
    expect(quiz.questions).toHaveLength(1);
    expect(quiz.texts.thanks).toBe("мерси");
  });

  it.each([
    [[{ key: "a", q: "Вопрос?", options: ["один"] }], "от 2 до 8 вариантов"],
    [[{ key: "a", q: "Вопрос?", options: ["1", "2"] }, { key: "a", q: "Ещё?", options: ["1", "2"] }], "повторяется"],
    [[{ key: "a", q: "?", options: ["1", "2"] }], "от 3 до 300"],
  ])("ошибка в вопросах называет вопрос и правило", (data, problem) => {
    expect(() => validate(data)).toThrow(BadQuiz);
    expect(() => validate(data)).toThrow(problem);
  });
});

describe("карта", () => {
  const answers = Object.fromEntries(DEFAULT.questions.map((q) => [q.key, q.options[0]]));

  it("промпт — тот же файл, что читает Python-версия, без переводов строк Windows", () => {
    expect(SYSTEM).toContain("карта автоматизаций");
    expect(SYSTEM).toContain("<<DATA:метка>>");
    expect(SYSTEM).not.toContain("\r");
  });

  it("ответы в случайной рамке и все вопросы в тексте", () => {
    expect(fenced("x")).not.toBe(fenced("x"));
    expect(fenced("x")).toMatch(/^<<DATA:[0-9a-f]{16}>>\nx\n<<\/DATA:[0-9a-f]{16}>>$/);
    const formatted = formatAnswers(DEFAULT.questions, answers);
    for (const q of DEFAULT.questions) expect(formatted).toContain(q.q);
  });

  it("у каждой боли из квиза — живое демо, у остальных — все проекты", () => {
    const pains = DEFAULT.questions.find((q) => q.key === "pain")!.options;
    for (const pain of pains) expect(withDemo("карта", { pain })).toMatch(/https:\/\/\S+$/);
    expect(withDemo("карта", { pain: "Запись и напоминания" })).toContain("/glyanets/");
  });

  it("не ответила первая модель — берётся следующая; не ответил никто — null", async () => {
    const fetch = vi.spyOn(globalThis, "fetch");
    fetch.mockResolvedValueOnce(Response.json({ error: { message: "rate limit" } }, { status: 429 }));
    fetch.mockResolvedValueOnce(Response.json({ choices: [{ message: { content: "🔍 карта" } }] }));
    expect(await buildMap("key", ["a:free", "b:free"], DEFAULT.questions, answers)).toBe("🔍 карта");
    expect(JSON.parse(String(fetch.mock.calls[1][1]!.body)).model).toBe("b:free");

    fetch.mockRejectedValue(new Error("timeout"));
    expect(await buildMap("key", ["a:free"], DEFAULT.questions, answers)).toBeNull();
  });
});

const LEAD: Lead = {
  id: 1,
  user_id: 42,
  username: "polina",
  name: "Полина",
  phone: "+79991112233",
  source: "reels",
  answers: { sphere: "Онлайн-школа / эксперт / блог" },
  at: 1_800_000_000,
  sent: 0,
  sheet: 0,
};

describe("выгрузка лидов", () => {
  const crm = { ...env, CRM_WEBHOOK_URL: "https://crm.example/webhook/lead", CRM_WEBHOOK_SECRET: "shhh" };

  it("в AI-CRM лид уходит с подписью, которую CRM проверит своим секретом", async () => {
    const fetch = vi.spyOn(globalThis, "fetch").mockResolvedValue(Response.json({ id: 17 }));
    expect(await sendCrm(crm, DEFAULT.questions, LEAD)).toBe(17);

    const [, init] = fetch.mock.calls[0];
    const headers = new Headers(init!.headers);
    const body = String(init!.body);
    expect(headers.get("x-signature")).toBe(await sign("shhh", body, headers.get("x-timestamp")!));
    expect(JSON.parse(body)).toMatchObject({ contact: "+79991112233", source: "квиз-бот", utm_source: "reels", consent: true });
  });

  it("без телефона контакт — ник, без согласия на звонок", () => {
    expect(crmPayload(DEFAULT.questions, { ...LEAD, phone: null })).toMatchObject({ contact: "@polina", consent: false });
  });

  it("ошибка CRM оставляет лид неотправленным, а не роняет бота", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("down", { status: 502 }));
    expect(await sendCrm(crm, DEFAULT.questions, LEAD)).toBeNull();
  });

  it("строка в Google Sheets — с датой по Москве и колонкой на вопрос", async () => {
    const fetch = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("ok"));
    const sheets = { ...env, SHEETS_WEBHOOK_URL: "https://script.google.com/x", SHEETS_TOKEN: "t" };
    expect(await sendSheet(sheets, DEFAULT.questions, LEAD)).toBe(true);
    const { row } = JSON.parse(String(fetch.mock.calls[0][1]!.body));
    expect(row["Дата"]).toBe("15.01.2027 11:00");
    expect(row[DEFAULT.questions[0].q]).toBe("Онлайн-школа / эксперт / блог");
  });
});

describe("вход вебхука", () => {
  it("без секрета Telegram запрос отклоняется", async () => {
    const response = await worker.fetch(
      new Request("https://quiz.example/telegram", { method: "POST", body: "{}" }),
      env,
    );
    expect(response.status).toBe(403);
  });

  it("проверка здоровья отвечает", async () => {
    const response = await worker.fetch(new Request("https://quiz.example/health"), env);
    expect(await response.json()).toEqual({ status: "ok" });
  });
});
