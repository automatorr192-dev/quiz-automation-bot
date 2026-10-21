import type { Question } from "./quiz";
import type { Lead } from "./store";

const MSK = 3 * 3600;

export const crmEnabled = (env: Env) => Boolean(env.CRM_WEBHOOK_URL && env.CRM_WEBHOOK_SECRET);
export const sheetsEnabled = (env: Env) => Boolean(env.SHEETS_WEBHOOK_URL);

export function crmPayload(questions: Question[], lead: Lead) {
  const lines = ["Квиз «карта автоматизаций»:"];
  lines.push(...questions.map((item) => `• ${item.q} — ${lead.answers[item.key] ?? "—"}`));
  const contact = lead.phone || (lead.username ? `@${lead.username}` : `tg id ${lead.user_id}`);
  return {
    text: lines.join("\n"),
    name: lead.name,
    contact,
    source: "квиз-бот",
    utm_source: lead.source && lead.source !== "—" ? lead.source : null,
    consent: Boolean(lead.phone),
  };
}

/** Подпись как у AI-CRM: HMAC-SHA256 от «время.тело» — подделать заявку без секрета нельзя. */
export async function sign(secret: string, body: string, timestamp: string): Promise<string> {
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const mac = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(`${timestamp}.${body}`));
  return [...new Uint8Array(mac)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

export async function sendCrm(env: Env, questions: Question[], lead: Lead): Promise<number | null> {
  if (!crmEnabled(env)) return null;
  const body = JSON.stringify(crmPayload(questions, lead));
  const timestamp = String(Math.floor(Date.now() / 1000));
  try {
    const response = await fetch(env.CRM_WEBHOOK_URL, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "x-timestamp": timestamp,
        "x-signature": await sign(env.CRM_WEBHOOK_SECRET, body, timestamp),
      },
      body,
      signal: AbortSignal.timeout(10_000),
    });
    if (!response.ok) throw new Error(`CRM ответила ${response.status}`);
    return (await response.json<{ id: number }>()).id;
  } catch (error) {
    console.error(`лид не ушёл в CRM: ${error}`);
    return null;
  }
}

function date(at: number): string {
  const moment = new Date((at + MSK) * 1000);
  const two = (value: number) => String(value).padStart(2, "0");
  return (
    `${two(moment.getUTCDate())}.${two(moment.getUTCMonth() + 1)}.${moment.getUTCFullYear()} ` +
    `${two(moment.getUTCHours())}:${two(moment.getUTCMinutes())}`
  );
}

export function sheetRow(questions: Question[], lead: Lead): Record<string, string> {
  const row: Record<string, string> = {
    Дата: date(lead.at),
    Канал: "Telegram",
    Имя: lead.name ?? "",
    Ник: lead.username ? `@${lead.username}` : "",
    Телефон: lead.phone ?? "",
    Источник: lead.source ?? "",
  };
  for (const item of questions) row[item.q] = lead.answers[item.key] ?? "";
  return row;
}

export async function sendSheet(env: Env, questions: Question[], lead: Lead): Promise<boolean> {
  if (!sheetsEnabled(env)) return false;
  try {
    const response = await fetch(env.SHEETS_WEBHOOK_URL, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ token: env.SHEETS_TOKEN, row: sheetRow(questions, lead) }),
      signal: AbortSignal.timeout(15_000),
    });
    return response.status === 200 && (await response.text()).trim().startsWith("ok");
  } catch (error) {
    console.error(`строка не ушла в Google Sheets: ${error}`);
    return false;
  }
}

/** Выгрузка для владельца: CSV с BOM и точкой с запятой — так его без вопросов открывает Excel. */
export function leadsCsv(questions: Question[], leads: Lead[]): string {
  const cell = (value: unknown) => `"${String(value ?? "").replace(/"/g, '""')}"`;
  const head = ["Дата", "Имя", "Telegram", "Телефон", "Источник", ...questions.map((item) => item.q)];
  const rows = leads.map((lead) => [
    date(lead.at),
    lead.name,
    lead.username ? `@${lead.username}` : lead.user_id,
    lead.phone,
    lead.source,
    ...questions.map((item) => lead.answers[item.key]),
  ]);
  return "﻿" + [head, ...rows].map((row) => row.map(cell).join(";")).join("\r\n");
}
