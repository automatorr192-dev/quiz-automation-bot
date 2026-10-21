import { WorkflowEntrypoint, type WorkflowEvent, type WorkflowStep } from "cloudflare:workers";
import { deliverMap, type MapParams } from "./bot";
import { deps } from "./deps";
import { buildMap } from "./map";
import { load } from "./quiz";

const models = (list: string) =>
  list
    .split(",")
    .map((model) => model.trim())
    .filter(Boolean);

/** Модель думает до пары минут, а вебхук должен ответить Telegram сразу. Ожидание ответа
 * сети не тратит процессорное время Worker, поэтому долгий шаг бесплатен. Повтора у шага
 * нет: внутри и так перебор моделей по списку, а второй круг — это минуты тишины. */
export class MapWorkflow extends WorkflowEntrypoint<Env, MapParams> {
  async run(event: Readonly<WorkflowEvent<MapParams>>, step: WorkflowStep) {
    const params = event.payload;
    const context = deps(this.env);
    const text = await step.do(
      "собрать карту",
      { retries: { limit: 0, delay: 0 }, timeout: "5 minutes" },
      async () => {
        await context.api("sendChatAction", { chat_id: params.chatId, action: "typing" }).catch(() => {});
        const quiz = await load(context.db);
        return buildMap(this.env.OPENROUTER_API_KEY, models(this.env.MODELS), quiz.questions, params.answers);
      },
    );
    await step.do("отдать карту", { retries: { limit: 0, delay: 0 } }, () =>
      deliverMap(context, params, text),
    );
  }
}
