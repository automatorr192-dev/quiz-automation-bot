import type { Update } from "@grammyjs/types";
import { env } from "cloudflare:test";
import type { Deps, MapParams } from "../src/bot";
import type { Api } from "../src/telegram";

export const POLINA = { id: 42, is_bot: false, first_name: "Полина", username: "polina" };
export const ADMIN = "999";

/** Бот с подменённым Telegram: каждый вызов Bot API записывается, а не уходит в сеть. */
export function world(overrides: Record<string, string> = {}) {
  const calls: { method: string; payload: any }[] = [];
  const maps: MapParams[] = [];
  const files: ArrayBuffer[] = [];
  let clock = 1_800_000_000;
  let id = 0;
  const deps: Deps = {
    db: env.DB,
    env: { ...env, ...overrides } as Env,
    api: (async (method: string, payload: Record<string, unknown> = {}) => {
      calls.push({ method, payload });
      return method === "sendMessage" ? { message_id: ++id } : true;
    }) as Api,
    download: async () => files.shift()!,
    startMap: async (params) => {
      maps.push(params);
    },
    now: () => clock,
  };
  return {
    deps,
    calls,
    maps,
    files,
    tick: (seconds: number) => (clock += seconds),
    sent: (chatId: number | string = POLINA.id) =>
      calls.filter((c) => c.method === "sendMessage" && String(c.payload.chat_id) === String(chatId)),
    last: (chatId: number | string = POLINA.id) => {
      const messages = calls.filter(
        (c) => c.method === "sendMessage" && String(c.payload.chat_id) === String(chatId),
      );
      return messages[messages.length - 1]?.payload;
    },
  };
}

let update = 0;

export function text(value: string, from = POLINA, chatId: number = from.id): Update {
  return {
    update_id: ++update,
    message: {
      message_id: update,
      date: 1,
      chat: { id: chatId, type: "private", first_name: from.first_name },
      from,
      text: value,
    },
  } as Update;
}

export function press(data: string, from = POLINA, date = 1): Update {
  return {
    update_id: ++update,
    callback_query: {
      id: `cb-${update}`,
      from,
      chat_instance: "x",
      data,
      message: { message_id: 7, date, chat: { id: from.id, type: "private", first_name: from.first_name }, text: "…" },
    },
  } as Update;
}

export function contact(phone: string, userId: number, from = POLINA): Update {
  return {
    update_id: ++update,
    message: {
      message_id: update,
      date: 1,
      chat: { id: from.id, type: "private", first_name: from.first_name },
      from,
      contact: { phone_number: phone, first_name: from.first_name, user_id: userId },
    },
  } as Update;
}

export function document(name: string, size = 500, from = { ...POLINA, id: Number(ADMIN) }): Update {
  return {
    update_id: ++update,
    message: {
      message_id: update,
      date: 1,
      chat: { id: from.id, type: "private", first_name: from.first_name },
      from,
      document: { file_id: "f1", file_unique_id: "u1", file_name: name, file_size: size },
    },
  } as Update;
}
