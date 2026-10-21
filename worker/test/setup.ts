import { applyD1Migrations, env } from "cloudflare:test";
import { beforeEach } from "vitest";

await applyD1Migrations(env.DB, env.TEST_MIGRATIONS);

beforeEach(async () => {
  await env.DB.batch(
    ["sessions", "events", "leads", "quota", "config"].map((table) => env.DB.prepare(`delete from ${table}`)),
  );
});
