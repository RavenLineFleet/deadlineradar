import path from "node:path";
import { defineConfig } from "vitest/config";
import { cloudflareTest, readD1Migrations } from "@cloudflare/vitest-pool-workers";

export default defineConfig(async () => {
  const migrationsPath = path.join(__dirname, "migrations");
  const migrations = await readD1Migrations(migrationsPath);
  return {
    test: {
      setupFiles: ["./test/apply-migrations.ts"],
      // AssetLab (2026-09-23), corrected by AuditLab TEST-1: the "9
      // remaining known-failing tests" (a DIFFERENT small, random set of
      // tests failing on every full-79-file-suite run, never the same
      // files twice, always clean in isolation) is real parallel-load
      // resource contention, not a per-test logic bug -- vitest-pool-
      // workers spins up one workerd instance per file, and this file's
      // default `maxWorkers` (unset -- vitest defaults to CPU count, 32 on
      // this box) let up to 32 of them contend for CPU/IO at once, which
      // also crashed a workerd worker outright in one of AuditLab's
      // measurement runs. That is the actual cause; the testTimeout bump
      // below (my first attempt) could only ever mask it for tests that
      // don't set their own timeout -- AuditLab TEST-1 (2026-09-23) proved
      // the bulk rate-limit tests that kept failing under load ALL carry
      // an explicit `it(..., ms)`, which always overrides this default, so
      // raising it never reached them. Capping maxWorkers addresses the
      // contention directly instead (orchestrator ruling, same date) --
      // this value is deliberately modest, not "as many as this box's 32
      // cores allow," because other agents can run their own full suite on
      // the same shared machine concurrently (confirmed live by AuditLab's
      // own measurement being contaminated by another session's orphaned
      // workerd processes).
      maxWorkers: 4,
      // Brought down from 15000 (orchestrator ruling: "once it's stable,
      // bring the inflated per-test timeouts back down to something that
      // still catches a real hang") after verifying maxWorkers:4 actually
      // fixes the contention -- a full clean run held steady at 3-4
      // workerd.exe processes throughout (vs. the old ~31-32) and came
      // back 79/79 files, 2589/2589 tests, judged by vitest's own exit
      // code (0) and passed==total, not a grep for FAIL. Kept modestly
      // above vitest's bare 5000 default, not restored to it exactly, as a
      // safety margin for a shared machine other agents may also be using.
      testTimeout: 8000,
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
