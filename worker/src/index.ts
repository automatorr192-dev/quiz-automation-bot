import type { Update } from "@grammyjs/types";
import { ensureWebhook, followUp, safely } from "./bot";
import { deps } from "./deps";

export { MapWorkflow } from "./flow";

async function same(a: string, b: string): Promise<boolean> {
  const [left, right] = [a, b].map((text) => new TextEncoder().encode(text));
  return left.byteLength === right.byteLength && crypto.subtle.timingSafeEqual(left, right);
}

export default {
  async fetch(request, env) {
    const { pathname } = new URL(request.url);
    if (pathname === "/health") return Response.json({ status: "ok" });
    if (request.method !== "POST" || pathname !== "/telegram") {
      return new Response("Not found", { status: 404 });
    }
    // Секрет, заданный в setWebhook, Telegram присылает в каждом запросе: без проверки
    // любой, кто узнал адрес, мог бы слать боту поддельные нажатия и лиды.
    const secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token") ?? "";
    if (!(await same(secret, env.WEBHOOK_SECRET))) return new Response("Forbidden", { status: 403 });
    // Ответ 200 даже при ошибке внутри: иначе Telegram повторяет апдейт снова и снова.
    await safely(await request.json<Update>(), deps(env));
    return new Response("ok");
  },

  async scheduled(_controller, env) {
    const context = deps(env);
    await ensureWebhook(context);
    await followUp(context);
  },
} satisfies ExportedHandler<Env>;
