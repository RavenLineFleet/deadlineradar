/**
 * RL-9 (AuditLab, 2026-09-26, LOW, originated with SecurityLab): checkRateLimit()'s
 * own cleanup (validation.ts) only reclaims rows for the (ip, bucket) key
 * being checked in the CURRENT request -- an address that hits a bucket
 * once and never comes back keeps its rows forever, so migration 0002's
 * old "self-trims rather than growing forever" claim was false for that
 * case. Covers both halves, same shape as SESS-3's own test file:
 *   1. store.purgeStaleRateLimitHits() -- unit-correct on its own.
 *   2. it's actually WIRED into scheduled() (MON-5's own lesson: a cron
 *      pass unit-tested but never dispatched proves nothing about
 *      production).
 */
import { env } from "cloudflare:test";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as store from "../src/store";

async function insertHit(ip: string, bucket: string, ts: number): Promise<void> {
  await env.DB.prepare(`INSERT INTO rate_limit_hits (ip, bucket, ts) VALUES (?1, ?2, ?3)`).bind(ip, bucket, ts).run();
}

describe("store.purgeStaleRateLimitHits", () => {
  // Full isolation between cases -- this is a global, unscoped sweep by
  // design, so a row left over from an earlier test would otherwise
  // silently inflate a later test's delete count.
  beforeEach(async () => {
    await env.DB.prepare("DELETE FROM rate_limit_hits WHERE ip LIKE 'rl9-%'").run();
  });

  it("deletes rows past the retention cutoff, leaves recent rows alone", async () => {
    const nowSeconds = 1_800_000_000;

    // 1 second past the default 86400s retention -- must be deleted.
    await insertHit("rl9-old", "subscribe", nowSeconds - 86_401);
    // 1 second within the retention window -- must survive.
    await insertHit("rl9-recent", "subscribe", nowSeconds - 86_399);
    // Fresh -- must survive regardless.
    await insertHit("rl9-fresh", "subscribe", nowSeconds - 10);

    const deleted = await store.purgeStaleRateLimitHits(env.DB, nowSeconds);
    expect(deleted).toBe(1);

    const remainingIps = (
      await env.DB.prepare("SELECT ip FROM rate_limit_hits WHERE ip LIKE 'rl9-%' ORDER BY ip").all<{ ip: string }>()
    ).results.map((r) => r.ip);
    expect(remainingIps).toEqual(["rl9-fresh", "rl9-recent"]);
  });

  it("respects a custom retentionSeconds", async () => {
    const nowSeconds = 1_800_000_000;
    await insertHit("rl9-custom", "subscribe", nowSeconds - 3_700); // ~1h2m old

    const noDelete = await store.purgeStaleRateLimitHits(env.DB, nowSeconds, 3_600 * 2);
    expect(noDelete).toBe(0);

    const doesDelete = await store.purgeStaleRateLimitHits(env.DB, nowSeconds, 3_600);
    expect(doesDelete).toBe(1);
  });

  it("reclaims a key that never recurs -- the exact gap checkRateLimit()'s own keyed delete cannot close", async () => {
    const nowSeconds = 1_800_000_000;
    // A single-visit address, one bucket, never hit again -- checkRateLimit()
    // never revisits this (ip, bucket) pair, so its own DELETE never fires.
    await insertHit("rl9-one-visit", "newsletter_subscribe", nowSeconds - 200_000);

    const deleted = await store.purgeStaleRateLimitHits(env.DB, nowSeconds);
    expect(deleted).toBe(1);
    const row = await env.DB.prepare("SELECT ip FROM rate_limit_hits WHERE ip = 'rl9-one-visit'").first();
    expect(row).toBeNull();
  });
});

describe("RL-9: the purge is actually WIRED into scheduled(), not just unit-correct", () => {
  it("invoking the real Worker cron entrypoint deletes a genuinely stale rate_limit_hits row via a real D1 write", async () => {
    await env.DB.prepare("DELETE FROM rate_limit_hits WHERE ip = 'rl9-wired-old'").run();
    await insertHit("rl9-wired-old", "subscribe", Math.floor(Date.UTC(2020, 0, 1) / 1000)); // far past any retention window

    const worker = (await import("../src/index")).default;
    const logSpy = vi.spyOn(console, "log").mockImplementation(() => {});
    const waited: Promise<unknown>[] = [];
    const ctx = { waitUntil: (p: Promise<unknown>) => waited.push(p) } as unknown as ExecutionContext;
    // No SENDGRID/RESEND key needed -- this pass sends no email at all,
    // same "unrelated concern sharing a trigger" posture as account-deletion
    // and session-purge.
    try {
      await worker.scheduled({} as ScheduledController, env as never, ctx);
      await Promise.allSettled(waited);

      const row = await env.DB.prepare("SELECT ip FROM rate_limit_hits WHERE ip = 'rl9-wired-old'").first();
      expect(row).toBeNull();
      expect(logSpy.mock.calls.some((call) => String(call[0]).includes("[rate-limit-purge-cron]"))).toBe(true);
    } finally {
      logSpy.mockRestore();
    }
  });
});
