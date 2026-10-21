import type { Deps } from "./bot";
import { download, telegram } from "./telegram";

export function deps(env: Env): Deps {
  const api = telegram(env.TELEGRAM_BOT_TOKEN);
  return {
    db: env.DB,
    api,
    env,
    download: (fileId) => download(env.TELEGRAM_BOT_TOKEN, api, fileId),
    startMap: async (params) => {
      await env.MAP.create({ params });
    },
    now: () => Date.now() / 1000,
  };
}
