import demos from "../../content/demos.json";
import prompt from "../../content/map-prompt.txt";
import type { Question } from "./quiz";
import type { Answers } from "./store";

// Тот же промпт, что читают Python-версия и эвалы: эвал проверяет ровно то, что работает в бою.
export const SYSTEM = prompt.replace(/\r\n/g, "\n");

// Ссылку на живое демо под главную боль ставит код, а не модель: модель могла бы её выдумать.
const DEMOS: Record<string, string[]> = demos.demos;

export function withDemo(text: string, answers: Answers): string {
  const [title, url] = DEMOS[answers.pain] ?? demos.fallback;
  return `${text}\n\n👀 Как это выглядит вживую: ${title}\n${url}`;
}

export function formatAnswers(questions: Question[], answers: Answers): string {
  return questions.map((item) => `${item.q} ${answers[item.key] ?? "—"}`).join("\n");
}

/** Метка случайная: закрыть блок изнутри и дописать свои инструкции снаружи не выйдет. */
export function fenced(text: string): string {
  const tag = [...crypto.getRandomValues(new Uint8Array(8))]
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
  return `<<DATA:${tag}>>\n${text}\n<</DATA:${tag}>>`;
}

/** Карта или null, если не ответила ни одна модель. Модели идут по списку: бесплатные часто
 * перегружены. Текст ошибки — только в лог: в нём внутренности API и id аккаунта. */
export async function buildMap(
  apiKey: string,
  models: string[],
  questions: Question[],
  answers: Answers,
): Promise<string | null> {
  const user = "Ответы человека:\n" + fenced(formatAnswers(questions, answers));
  for (const model of models) {
    try {
      const response = await fetch("https://openrouter.ai/api/v1/chat/completions", {
        method: "POST",
        headers: { authorization: `Bearer ${apiKey}`, "content-type": "application/json" },
        body: JSON.stringify({
          model,
          max_tokens: 4000,
          messages: [
            { role: "system", content: SYSTEM },
            { role: "user", content: user },
          ],
        }),
        signal: AbortSignal.timeout(90_000),
      });
      const data = await response.json<any>();
      const content = data?.choices?.[0]?.message?.content;
      if (content) {
        console.log(`карта собрана: ${model}, ${data.usage?.prompt_tokens}→${data.usage?.completion_tokens} токенов`);
        return content;
      }
      console.warn(`модель ${model} не ответила: ${response.status} ${data?.error?.message ?? ""}`);
    } catch (error) {
      console.warn(`модель ${model} не ответила: ${error}`);
    }
  }
  console.error("карта не собрана ни одной моделью");
  return null;
}
