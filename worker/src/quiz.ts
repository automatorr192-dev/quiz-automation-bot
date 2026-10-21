import content from "../../content/quiz.json";
import { getConfig, setConfig } from "./store";

export const MAX_QUESTIONS = 12;
export const MAX_OPTIONS = 8;

export interface Question {
  key: string;
  q: string;
  options: string[];
  hint?: string;
}
export type Texts = Record<"welcome" | "finish" | "phone" | "thanks", string>;
export interface Quiz {
  questions: Question[];
  texts: Texts;
}

// Вопросы и тексты по умолчанию — общие с Python-версией и эвалами, лежат в content/.
export const DEFAULT: Quiz = content as Quiz;

export class BadQuiz extends Error {}

const TAGS = new Set(["b", "i", "u", "s", "a", "code", "pre", "blockquote", "tg-spoiler"]);
const TAG = /<(\/?)([a-z-]+)(?:\s[^<>]*)?>/g;

const isText = (value: unknown, min: number, max: number): value is string =>
  typeof value === "string" && value.trim().length >= min && value.trim().length <= max;

export function validate(data: unknown): Question[] {
  if (!Array.isArray(data) || data.length < 1 || data.length > MAX_QUESTIONS) {
    throw new BadQuiz(`нужен список из 1–${MAX_QUESTIONS} вопросов`);
  }
  const keys = new Set<string>();
  return data.map((item, index) => {
    const number = index + 1;
    if (typeof item !== "object" || item === null || Array.isArray(item)) {
      throw new BadQuiz(`вопрос ${number}: ожидается объект с полями key, q, options`);
    }
    const { key, q, options, hint } = item as Record<string, unknown>;
    if (typeof key !== "string" || !key.trim() || keys.has(key)) {
      throw new BadQuiz(`вопрос ${number}: key пустой или повторяется`);
    }
    if (!isText(q, 3, 300)) {
      throw new BadQuiz(`вопрос ${number}: текст вопроса от 3 до 300 знаков`);
    }
    if (
      !Array.isArray(options) ||
      options.length < 2 ||
      options.length > MAX_OPTIONS ||
      !options.every((option) => isText(option, 1, 60))
    ) {
      throw new BadQuiz(`вопрос ${number}: от 2 до ${MAX_OPTIONS} вариантов по 1–60 знаков`);
    }
    if (hint !== undefined && hint !== null && (typeof hint !== "string" || hint.length > 200)) {
      throw new BadQuiz(`вопрос ${number}: подсказка до 200 знаков`);
    }
    keys.add(key);
    const entry: Question = {
      key: key.trim(),
      q: q.trim(),
      options: options.map((option: string) => option.trim()),
    };
    if (hint) entry.hint = (hint as string).trim();
    return entry;
  });
}

export function htmlOk(text: string): boolean {
  const stack: string[] = [];
  const tags = [...text.matchAll(TAG)];
  for (const [, closing, name] of tags) {
    if (!TAGS.has(name)) return false;
    if (closing) {
      if (stack.pop() !== name) return false;
    } else stack.push(name);
  }
  return stack.length === 0 && text.split("<").length - 1 === tags.length;
}

export function validateTexts(data: unknown): Texts {
  if (typeof data !== "object" || data === null || Array.isArray(data)) {
    throw new BadQuiz("texts — объект с текстами бота");
  }
  const clean = { ...DEFAULT.texts };
  for (const [key, value] of Object.entries(data)) {
    if (!(key in clean)) {
      throw new BadQuiz(`неизвестный текст «${key}», есть: ${Object.keys(clean).join(", ")}`);
    }
    if (!isText(value, 1, 1500)) throw new BadQuiz(`текст «${key}» от 1 до 1500 знаков`);
    if (!htmlOk(value)) {
      throw new BadQuiz(
        `в тексте «${key}» сломана разметка: теги b, i, u, s, a, code должны ` +
          "закрываться, а знак < писать как &lt;",
      );
    }
    clean[key as keyof Texts] = value.trim();
  }
  return clean;
}

/** Файл от владельца: { questions, texts } или старый формат — просто список вопросов. */
export function parse(data: unknown, current: Texts): Quiz {
  if (Array.isArray(data)) return { questions: validate(data), texts: { ...current } };
  if (typeof data === "object" && data !== null && "questions" in data) {
    const { questions, texts } = data as { questions: unknown; texts?: unknown };
    return { questions: validate(questions), texts: validateTexts(texts ?? {}) };
  }
  throw new BadQuiz('ожидается {"questions": [...], "texts": {...}} или список вопросов');
}

export async function load(db: D1Database): Promise<Quiz> {
  const stored = await getConfig(db, "quiz");
  if (!stored) return DEFAULT;
  try {
    return parse(JSON.parse(stored), DEFAULT.texts);
  } catch {
    return DEFAULT;
  }
}

export async function save(db: D1Database, quiz: Quiz): Promise<void> {
  await setConfig(db, "quiz", JSON.stringify(quiz));
}

export function progressBar(step: number, total: number): string {
  return "▰".repeat(step + 1) + "▱".repeat(total - step - 1) + `  ${step + 1}/${total}`;
}
