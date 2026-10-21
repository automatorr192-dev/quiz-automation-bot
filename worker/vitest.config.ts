import { cloudflareTest, readD1Migrations } from "@cloudflare/vitest-plugin";
import { defineConfig } from "vitest/config";

export default defineConfig(async () => {
  const migrations = await readD1Migrations("./migrations");
  return {
    plugins: [
      cloudflareTest({
        remoteBindings: false,
        wrangler: { configPath: "./wrangler.jsonc" },
        miniflare: {
          bindings: {
            TEST_MIGRATIONS: migrations,
            TELEGRAM_BOT_TOKEN: "123:test",
            WEBHOOK_SECRET: "test-secret",
            OPENROUTER_API_KEY: "test",
            ADMIN_CHAT_ID: "999",
            CRM_WEBHOOK_URL: "",
            CRM_WEBHOOK_SECRET: "",
            SHEETS_WEBHOOK_URL: "",
            SHEETS_TOKEN: "",
          },
        },
      }),
    ],
    test: { setupFiles: ["./test/setup.ts"] },
  };
});
