import path from "node:path";
import { defineConfig } from "vitest/config";
import { cloudflareTest, readD1Migrations } from "@cloudflare/vitest-pool-workers";

export default defineConfig(async () => {
  const migrationsPath = path.join(__dirname, "migrations");
  const migrations = await readD1Migrations(migrationsPath);
  return {
    test: {
      setupFiles: ["./test/apply-migrations.ts"],
      // AssetLab (2026-09-23): root cause of the "9 remaining known-failing
      // tests" from AuditLab's/SecurityLab's reports -- a DIFFERENT small,
      // random set of tests times out at exactly the default 5000ms on
      // every full-79-file-suite run (confirmed across 3 separate runs:
      // never the same files twice, never a test this session's changes
      // touched, always passing clean in isolation or in a small subset).
      // That signature is real parallel-load resource contention on this
      // machine (vitest-pool-workers spins up one workerd instance per
      // file; ~79 of them contending for CPU/IO at once), not a per-test
      // logic bug -- so per-test quarantining was the wrong fix (whichever
      // tests lose the scheduling lottery isn't stable) and per-test
      // timeout bumps would be permanent whack-a-mole against a moving
      // target. A single generous global default absorbs the contention
      // for every test at once, while still catching a genuinely hung test
      // (3x the vitest default, not unbounded) -- explicit per-test
      // timeouts elsewhere in this suite (20000/30000/60000ms, for tests
      // with a documented reason of their own) are unaffected, since an
      // explicit `it(..., ms)` always overrides this default.
      testTimeout: 15000,
    },
    plugins: [
      cloudflareTest({
        wrangler: { configPath: "./wrangler.toml" },
        miniflare: {
          bindings: { TEST_MIGRATIONS: migrations },
        },
      }),
    ],
  };
});
