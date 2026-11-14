/**
 * SEC-96 (SecurityLab, confirmed MEDIUM by AuditLab 2026-11-14): a reminder
 * tier claim stranded by a swallowed last-resort unclaim used to be lost
 * forever -- the tier sat in reminders_sent, so nextDueThreshold() skipped it
 * and claimReminderThreshold()'s pure-JS early return (`sent.includes`) never
 * reached the database. Migration 0093's reminder_claims lease makes that
 * state recoverable: an open claim older than REMINDER_CLAIM_LEASE_MS is
 * re-leased and re-sent on the next pass.
 */
import { env } from "cloudflare:test";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as store from "../src/store";
import type { BuiltEmail } from "../src/emails";

const MS_PER_DAY = 86_400_000;

// Each test gets its own asOf, years apart, so no other test's subscriber
// lands on the same due date inside the same pass.
let asOfCounter = 0;
function freshAsOf(): Date {
  asOfCounter += 1;
  return new Date(Date.UTC(2040 + asOfCounter * 3, 2, 10));
}

function isoDaysAfter(base: Date, days: number): string {
  return new Date(base.getTime() + days * MS_PER_DAY).toISOString().slice(0, 10);
}

async function seedSubscriber(tag: string, asOf: Date, daysOut = 7, mode: string | null = null): Promise<{ id: string; email: string }> {
  const email = `sec96-${tag}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}@example.com`;
  const rec = await store.addPending(env.DB, {
    email,
    stateSlug: "texas",
    deadlineFields: {},
    deadlineSource: store.DEADLINE_SOURCE_USER,
    userDeadline: isoDaysAfter(asOf, daysOut),
    firstName: null,
    firmId: null,
    skipConfirmation: true,
  });
  await env.DB.prepare("UPDATE subscribers SET status = ?1 WHERE id = ?2").bind(store.STATUS_CONFIRMED, rec.id).run();
  if (mode) {
    await env.DB.prepare("UPDATE subscribers SET notification_mode = ?1 WHERE id = ?2").bind(mode, rec.id).run();
  }
  return { id: rec.id, email: store.normalizeEmail(email) };
}

async function remindersSent(id: string): Promise<number[]> {
  const row = await env.DB.prepare("SELECT reminders_sent FROM subscribers WHERE id = ?1").bind(id).first<{ reminders_sent: string }>();
  return JSON.parse(row?.reminders_sent ?? "[]");
}

async function leaseRows(id: string) {
  const { results } = await env.DB.prepare(
    "SELECT cycle, threshold_days, claimed_at, completed_at FROM reminder_claims WHERE subscriber_id = ?1 ORDER BY threshold_days"
  )
    .bind(id)
    .all<{ cycle: number; threshold_days: number; claimed_at: number; completed_at: number | null }>();
  return results;
}

async function ageLeases(id: string, byMs = store.REMINDER_CLAIM_LEASE_MS + 60_000): Promise<void> {
  await env.DB.prepare("UPDATE reminder_claims SET claimed_at = claimed_at - ?1 WHERE subscriber_id = ?2").bind(byMs, id).run();
}

function recordingSend(): { send: (to: string, built: BuiltEmail) => Promise<boolean>; sentTo: string[] } {
  const sentTo: string[] = [];
  return {
    sentTo,
    send: async (to: string) => {
      sentTo.push(store.normalizeEmail(to));
      return true;
    },
  };
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("SEC-96 -- runReminderPass recovers a claim stranded by a swallowed unclaim", () => {
  it("strands a tier, holds it inside the lease, then re-sends it once the lease ages out (and the lease SELECT runs)", async () => {
    const { runReminderPass } = await import("../src/scheduler");
    const asOf = freshAsOf();
    const sub = await seedSubscriber("strand", asOf);

    // Pass 1: send() throws -> the catch's last-resort unclaim is the ONLY
    // revert, and it fails. Before SEC-96 this was `.catch(() => {})`.
    const unclaimSpy = vi.spyOn(store, "unclaimReminderThreshold").mockRejectedValueOnce(new Error("D1_ERROR: sec96 strand"));
    const logSpy = vi.spyOn(console, "log");
    await runReminderPass(env, {
      asOf,
      send: async (to) => {
        if (store.normalizeEmail(to) === sub.email) throw new Error("transport blew up");
        return true;
      },
    });
    // Compare ids only: chai can't inspect a D1Database binding.
    expect(unclaimSpy.mock.calls.some((c) => c[1] === sub.id && c[2] === 7)).toBe(true);
    expect(await remindersSent(sub.id)).toEqual([7]);
    const lease = await leaseRows(sub.id);
    expect(lease).toHaveLength(1);
    expect(lease[0]?.threshold_days).toBe(7);
    expect(lease[0]?.completed_at).toBeNull();
    // The swallow is now attributable.
    const logged = logSpy.mock.calls.map((c) => String(c[0]));
    expect(logged.some((l) => l.includes("[unclaim-failed] pass=reminder") && l.includes(`subscriber_id=${sub.id}`) && l.includes("threshold=7"))).toBe(true);
    unclaimSpy.mockRestore();

    // Pass 2, lease still fresh: an overlapping/retry pass must NOT resend.
    const early = recordingSend();
    await runReminderPass(env, { asOf, send: early.send });
    expect(early.sentTo.filter((e) => e === sub.email)).toHaveLength(0);

    // Pass 3, lease aged out: the stale-lease SELECT runs, finds this
    // subscriber, and the tier is re-sent exactly once and completed.
    await ageLeases(sub.id);
    const listSpy = vi.spyOn(store, "listStaleReminderClaims");
    const late = recordingSend();
    await runReminderPass(env, { asOf, send: late.send });
    expect(listSpy.mock.calls.length).toBeGreaterThan(0);
    const seen = await listSpy.mock.results[0]?.value;
    expect(seen?.get(sub.id)?.has(7)).toBe(true);
    expect(late.sentTo.filter((e) => e === sub.email)).toHaveLength(1);
    expect(await remindersSent(sub.id)).toEqual([7]);
    const after = await leaseRows(sub.id);
    expect(after).toHaveLength(1);
    expect(after[0]?.completed_at).not.toBeNull();

    // Pass 4: completed, even when aged -> never sent again.
    await ageLeases(sub.id);
    const again = recordingSend();
    await runReminderPass(env, { asOf, send: again.send });
    expect(again.sentTo.filter((e) => e === sub.email)).toHaveLength(0);
  });

  it("no backfill: a tier already in reminders_sent with NO lease row (pre-0093) is never re-sent", async () => {
    const { runReminderPass } = await import("../src/scheduler");
    const asOf = freshAsOf();
    const sub = await seedSubscriber("legacy", asOf);
    await env.DB.prepare("UPDATE subscribers SET reminders_sent = '[7]' WHERE id = ?1").bind(sub.id).run();
    const rec = recordingSend();
    await runReminderPass(env, { asOf, send: rec.send });
    expect(rec.sentTo.filter((e) => e === sub.email)).toHaveLength(0);
    expect(await leaseRows(sub.id)).toHaveLength(0);
  });

  it("a successful send completes its lease", async () => {
    const { runReminderPass } = await import("../src/scheduler");
    const asOf = freshAsOf();
    const sub = await seedSubscriber("complete", asOf);
    const rec = recordingSend();
    await runReminderPass(env, { asOf, send: rec.send });
    expect(rec.sentTo.filter((e) => e === sub.email)).toHaveLength(1);
    const lease = await leaseRows(sub.id);
    expect(lease).toHaveLength(1);
    expect(lease[0]?.completed_at).not.toBeNull();
  });

  it("send() returning false unclaims the tier AND deletes its open lease in the same batch", async () => {
    const { runReminderPass } = await import("../src/scheduler");
    const asOf = freshAsOf();
    const sub = await seedSubscriber("sendfalse", asOf);
    await runReminderPass(env, {
      asOf,
      send: async (to) => store.normalizeEmail(to) !== sub.email,
    });
    expect(await remindersSent(sub.id)).toEqual([]);
    expect(await leaseRows(sub.id)).toHaveLength(0);
  });

  it("accepted trade-off: if the completion write fails after a real send, the reminder goes out again after the lease ages (at-least-once, never lost)", async () => {
    const { runReminderPass } = await import("../src/scheduler");
    const asOf = freshAsOf();
    const sub = await seedSubscriber("completefail", asOf);
    const completeSpy = vi.spyOn(store, "completeReminderClaim").mockRejectedValueOnce(new Error("D1_ERROR: sec96 complete"));
    const first = recordingSend();
    await runReminderPass(env, { asOf, send: first.send });
    expect(completeSpy.mock.calls.length).toBeGreaterThan(0);
    expect(first.sentTo.filter((e) => e === sub.email)).toHaveLength(1);
    // The failed completion must NOT have triggered an unclaim.
    expect(await remindersSent(sub.id)).toEqual([7]);
    completeSpy.mockRestore();
    await ageLeases(sub.id);
    const second = recordingSend();
    await runReminderPass(env, { asOf, send: second.send });
    expect(second.sentTo.filter((e) => e === sub.email)).toHaveLength(1);
  });

  it("two overlapping passes over the same aged lease send exactly once", async () => {
    const { runReminderPass } = await import("../src/scheduler");
    const asOf = freshAsOf();
    const sub = await seedSubscriber("race", asOf);
    expect(await store.claimReminderThreshold(env.DB, sub.id, "[]", 7)).toBe(true);
    await ageLeases(sub.id);
    const rec = recordingSend();
    await Promise.all([runReminderPass(env, { asOf, send: rec.send }), runReminderPass(env, { asOf, send: rec.send })]);
    expect(rec.sentTo.filter((e) => e === sub.email)).toHaveLength(1);
  });
});

describe("SEC-96 -- claimReminderThreshold lease semantics", () => {
  it("fresh claim writes reminders_sent and an open lease atomically; a concurrent loser writes neither", async () => {
    const asOf = freshAsOf();
    const sub = await seedSubscriber("fresh", asOf);
    const results = await Promise.all([
      store.claimReminderThreshold(env.DB, sub.id, "[]", 7),
      store.claimReminderThreshold(env.DB, sub.id, "[]", 7),
    ]);
    expect(results.filter(Boolean)).toHaveLength(1);
    expect(await remindersSent(sub.id)).toEqual([7]);
    const lease = await leaseRows(sub.id);
    expect(lease).toHaveLength(1);
    expect(lease[0]?.completed_at).toBeNull();
  });

  it("a stale-snapshot loser does not insert a lease for a tier it never claimed", async () => {
    const asOf = freshAsOf();
    const sub = await seedSubscriber("snapshot", asOf);
    expect(await store.claimReminderThreshold(env.DB, sub.id, "[]", 7)).toBe(true);
    // Reads the old "[]" snapshot and tries tier 3: the UPDATE and the
    // INSERT share the predicate, so neither lands.
    expect(await store.claimReminderThreshold(env.DB, sub.id, "[]", 3)).toBe(false);
    expect(await remindersSent(sub.id)).toEqual([7]);
    expect((await leaseRows(sub.id)).map((r) => r.threshold_days)).toEqual([7]);
  });

  it("re-lease: a tier already in reminders_sent is won only when its lease is open AND stale, and only once", async () => {
    const asOf = freshAsOf();
    const sub = await seedSubscriber("relese", asOf);
    expect(await store.claimReminderThreshold(env.DB, sub.id, "[]", 7)).toBe(true);
    // Fresh open lease -> still in flight.
    expect(await store.claimReminderThreshold(env.DB, sub.id, "[7]", 7)).toBe(false);
    await ageLeases(sub.id);
    const results = await Promise.all([
      store.claimReminderThreshold(env.DB, sub.id, "[7]", 7),
      store.claimReminderThreshold(env.DB, sub.id, "[7]", 7),
    ]);
    expect(results.filter(Boolean)).toHaveLength(1);
    // Completed -> never re-leased, however old.
    await store.completeReminderClaim(env.DB, sub.id, 7);
    await ageLeases(sub.id);
    expect(await store.claimReminderThreshold(env.DB, sub.id, "[7]", 7)).toBe(false);
  });

  it("rearm: a stale open lease from the previous cycle is not listed and cannot be re-leased in the new cycle", async () => {
    const asOf = freshAsOf();
    const sub = await seedSubscriber("rearm", asOf);
    expect(await store.claimReminderThreshold(env.DB, sub.id, "[]", 7)).toBe(true);
    await ageLeases(sub.id);
    expect((await store.listStaleReminderClaims(env.DB)).get(sub.id)?.has(7)).toBe(true);
    // Same SET clause rearm()/applyRenewAndRearm() use (store.ts).
    await env.DB.prepare("UPDATE subscribers SET reminders_sent = '[]', cycle = cycle + 1 WHERE id = ?1").bind(sub.id).run();
    expect((await store.listStaleReminderClaims(env.DB)).has(sub.id)).toBe(false);
    // A new-cycle claim gets its own row, open and fresh.
    expect(await store.claimReminderThreshold(env.DB, sub.id, "[]", 7)).toBe(true);
    const rows = await leaseRows(sub.id);
    expect(rows).toHaveLength(2);
    expect(rows.every((r) => r.threshold_days === 7)).toBe(true);
  });

  it("resetConfirmation (reminders_sent reset, no cycle bump): an old open lease cannot resend, and a fresh claim replaces it", async () => {
    const asOf = freshAsOf();
    const sub = await seedSubscriber("reset", asOf);
    expect(await store.claimReminderThreshold(env.DB, sub.id, "[]", 7)).toBe(true);
    await ageLeases(sub.id);
    await env.DB.prepare("UPDATE subscribers SET reminders_sent = '[]' WHERE id = ?1").bind(sub.id).run();
    // Re-lease path needs the tier in the CURRENT snapshot; it isn't.
    expect(await store.claimReminderThreshold(env.DB, sub.id, "[7]", 7)).toBe(false);
    // Fresh claim replaces the stale row: one row, open, claimed now.
    const before = Date.now();
    expect(await store.claimReminderThreshold(env.DB, sub.id, "[]", 7)).toBe(true);
    const rows = await leaseRows(sub.id);
    expect(rows).toHaveLength(1);
    expect(rows[0]?.completed_at).toBeNull();
    expect(rows[0]?.claimed_at).toBeGreaterThanOrEqual(before - 1000);
  });
});

describe("SEC-96 -- runDigestPass shares the lease", () => {
  it("re-sends a stranded digest tier after the lease ages, and completes it", async () => {
    const { runDigestPass } = await import("../src/scheduler");
    const asOf = freshAsOf();
    const sub = await seedSubscriber("digest", asOf, 2, store.NOTIFICATION_MODE_DIGEST);
    // Strand tier 3 directly: claimed, never completed, unclaim lost.
    expect(await store.claimReminderThreshold(env.DB, sub.id, "[]", 3)).toBe(true);
    const fresh = recordingSend();
    await runDigestPass(env, { asOf, send: fresh.send });
    expect(fresh.sentTo.filter((e) => e === sub.email)).toHaveLength(0);
    await ageLeases(sub.id);
    const late = recordingSend();
    await runDigestPass(env, { asOf, send: late.send });
    expect(late.sentTo.filter((e) => e === sub.email)).toHaveLength(1);
    const rows = await leaseRows(sub.id);
    expect(rows.find((r) => r.threshold_days === 3)?.completed_at).not.toBeNull();
  });
});
