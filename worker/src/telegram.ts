// Bot API без библиотеки: боту нужен десяток методов, а у бесплатного Worker 10 мс
// процессора на сообщение — фреймворк съел бы их на собственный запуск.

export type Api = <T = any>(method: string, payload?: Record<string, unknown>) => Promise<T>;

export class TelegramError extends Error {
  constructor(
    readonly method: string,
    readonly code: number,
    description: string,
  ) {
    super(`${method}: ${code} ${description}`);
  }
}

export function telegram(token: string): Api {
  return async (method, payload = {}) => {
    const upload = Object.values(payload).some((value) => value instanceof File);
    const response = await fetch(`https://api.telegram.org/bot${token}/${method}`, {
      method: "POST",
      ...(upload
        ? { body: form(payload) }
        : { headers: { "content-type": "application/json" }, body: JSON.stringify(payload) }),
    });
    const data = await response.json<{
      ok: boolean;
      result: any;
      error_code?: number;
      description?: string;
    }>();
    if (!data.ok) {
      throw new TelegramError(method, data.error_code ?? response.status, data.description ?? "");
    }
    return data.result;
  };
}

function form(payload: Record<string, unknown>): FormData {
  const body = new FormData();
  for (const [key, value] of Object.entries(payload)) {
    if (value instanceof File) body.append(key, value, value.name);
    else if (value !== undefined) {
      body.append(key, typeof value === "string" ? value : JSON.stringify(value));
    }
  }
  return body;
}

export async function download(token: string, api: Api, fileId: string): Promise<ArrayBuffer> {
  const file = await api<{ file_path: string }>("getFile", { file_id: fileId });
  const response = await fetch(`https://api.telegram.org/file/bot${token}/${file.file_path}`);
  return response.arrayBuffer();
}
