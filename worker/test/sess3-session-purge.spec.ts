/**
 * SESS-3 (AuditLab, 2026-09-19, LOW): AuditLab's session-lifecycle audit
 * came back clean -- expiry enforced fail-closed at read time on both
 * firm_sessions and subscriber_sessions, so an expired row can never
 * authenticate. The one gap: no purge, so rows accumulate indefinitely.
 * Not a security fix, a data-retention one. Covers both halves:
 *   1. store.purgeExpiredSessions() -- unit-correct on its own.
 *   2. it's actually WIRED into scheduled() (MON-5's own lesson: a cron
 *      pass unit-tested but never dispatched proves nothing about
 *      production).
 */
import { env } from "cloudflare:test";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as store from "../src/store";

async function insertFirmSession(id: string, firmId: string, expiresAt: string): Promise<void> {
  await env.DB.prepare(
    `INSERT INTO firm_sessions (id, firm_id, session_token_hash, created_at, expires_at, last_seen_at) VALUES (?1, ?2, ?3, ?4, ?5, ?4)`
  )
    .bind(id, firmId, `hash-${id}`, "2026-01-01T00:00:00.000Z", expiresAt)
    .run();
}

async function insertSubscriberSession(id: string, emailNormalized: string, expiresAt: string): Promise<void> {
  await env.DB.prepare(
    `INSERT INTO subscriber_sessions (id, email_normalized, session_token_hash, created_at, expires_at, last_seen_at) VALUES (?1, ?2, ?3, ?4, ?5, ?4)`
  )
    .bind(id, emailNormalized, `hash-${id}`, "2026-01-01T00:00:00.000Z", expiresAt)
    .run();
}

async function seedFirmForFk(): Promise<string> {
  const firm = await store.createFirm(env.DB, { name: "SESS-3 Purge Firm", adminEmail: `sess3-purge-firm-${Date.now()}-${Math.random()}@example.com` });
  return firm.id;
}

describe("store.purgeExpiredSessions", () => {
  // Full isolation between cases -- purgeExpiredSessions() is a global,
  // unscoped sweep by design, so a row left over from an earlier test in
  // this file would otherwise silently inflate a later test's delete count.
  beforeEach(async () => {
    await env.DB.prepare("DELETE FROM firm_sessions WHERE id LIKE 'sess3-%'").run();
    await env.DB.prepare("DELETE FROM subscriber_sessions WHERE id LIKE 'sess3-%'").run();
  });

  it("deletes rows past the retention cutoff, leaves recently-expired and still-live rows alone", async () => {
    const firmId = await seedFirmForFk();
    const asOf = new Date("2026-09-23T00:00:00.000Z");

    // 91 days before asOf -- past the 90-day retention cutoff, must be deleted.
    await insertFirmSession("sess3-old", firmId, "2026-06-24T00:00:00.000Z");
    await insertSubscriberSession("sess3-sub-old", "purge-old@example.com", "2026-06-24T00:00:00.000Z");
    // 89 days before asOf -- expired, but WITHIN the retention window, must survive.
    await insertFirmSession("sess3-recent-expired", firmId, "2026-06-26T00:00:00.000Z");
    await insertSubscriberSession("sess3-sub-recent-expired", "purge-recent@example.com", "2026-06-26T00:00:00.000Z");
    // Still live -- must survive regardless of retention window.
    await insertFirmSession("sess3-live", firmId, "2027-01-01T00:00:00.000Z");
    await insertSubscriberSession("sess3-sub-live", "purge-live@example.com", "2027-01-01T00:00:00.000Z");

    const result = await store.purgeExpiredSessions(env.DB, asOf);
    expect(result.firmSessionsDeleted).toBe(1);
    expect(result.subscriberSessionsDeleted).toBe(1);

    const remainingFirmIds = (await env.DB.prepare("SELECT id FROM firm_sessions WHERE firm_id = ?1 ORDER BY id").bind(firmId).all<{ id: string }>()).results.map((r) => r.id);
    expect(remainingFirmIds).toEqual(["sess3-live", "sess3-recent-expired"]);

    const remainingSubIds = (
      await env.DB.prepare("SELECT id FROM subscriber_sessions WHERE id LIKE 'sess3-sub-%' ORDER BY id").all<{ id: string }>()
    ).results.map((r) => r.id);
    expect(remainingSubIds).toEqual(["sess3-sub-live", "sess3-sub-recent-expired"]);
  });

  it("respects a custom retentionDaysPastExpiry", async () => {
    const firmId = await seedFirmForFk();
    const asOf = new Date("2026-09-23T00:00:00.000Z");
    await insertFirmSession("sess3-custom", firmId, "2026-09-01T00:00:00.000Z"); // 22 days before asOf

    const noDelete = await store.purgeExpiredSessions(env.DB, asOf, 90);
    expect(noDelete.firmSessionsDeleted).toBe(0);

    const doesDelete = await store.purgeExpiredSessions(env.DB, asOf, 20);
    expect(doesDelete.firmSessionsDeleted).toBe(1);
  });
});

describe("SESS-3: the purge is actually WIRED into scheduled(), not just unit-correct", () => {
  it("invoking the real Worker cron entrypoint deletes a genuinely stale session row via a real D1 write", async () => {
    const firmId = await seedFirmForFk();
    await insertFirmSession("sess3-wired-old", firmId, "2020-01-01T00:00:00.000Z"); // far past any retention window
    await insertSubscriberSession("sess3-wired-sub-old", "purge-wired@example.com", "2020-01-01T00:00:00.000Z");

    const worker = (await import("../src/index")).default;
    const logSpy = vi.spyOn(console, "log").mockImplementation(() => {});
    const waited: Promise<unknown>[] = [];
    const ctx = { waitUntil: (p: Promise<unknown>) => waited.push(p) } as unknown as ExecutionContext;
    // No SENDGRID key needed -- this pass sends no email at all, same
    // "unrelated concern sharing a trigger" posture as account-deletion.
    try {
      await worker.scheduled({} as ScheduledController, env as never, ctx);
      await Promise.allSettled(waited);

      const firmRow = await env.DB.prepare("SELECT id FROM firm_sessions WHERE id = ?1").bind("sess3-wired-old").first();
      expect(firmRow).toBeNull();
      const subRow = await env.DB.prepare("SELECT id FROM subscriber_sessions WHERE id = ?1").bind("sess3-wired-sub-old").first();
      expect(subRow).toBeNull();
      expect(logSpy.mock.calls.some((call) => String(call[0]).includes("[session-purge-cron]"))).toBe(true);
    } finally {
      logSpy.mockRestore();
    }
  });
});
