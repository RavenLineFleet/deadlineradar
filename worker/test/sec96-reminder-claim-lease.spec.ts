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

  it("send() returning false unclaims the tier AND retires (not deletes) its open lease in the same batch", async () => {
    const { runReminderPass } = await import("../src/scheduler");
    const asOf = freshAsOf();
    const sub = await seedSubscriber("sendfalse", asOf);
    await runReminderPass(env, {
      asOf,
      send: async (to) => store.normalizeEmail(to) !== sub.email,
    });
    expect(await remindersSent(sub.id)).toEqual([]);
    const rows = await leaseRows(sub.id);
    expect(rows).toHaveLength(1);
    expect(rows[0]?.claimed_at).toBe(0);
    expect(rows[0]?.completed_at).toBeNull();
    // The next pass re-claims the tier over the retired row (ON CONFLICT,
    // not a PK failure that rolls back the reminders_sent UPDATE).
    const rec = recordingSend();
    await runReminderPass(env, { asOf, send: rec.send });
    expect(rec.sentTo.filter((e) => e === sub.email)).toHaveLength(1);
    expect(await remindersSent(sub.id)).toEqual([7]);
    const after = await leaseRows(sub.id);
    expect(after).toHaveLength(1);
    expect(after[0]?.completed_at).not.toBeNull();
  });

  it("undo symmetry (AuditLab #1): a lost-edit restore that puts an unclaimed tier back into reminders_sent is recovered, not read as completed", async () => {
    const { runReminderPass } = await import("../src/scheduler");
    const asOf = freshAsOf();
    const sub = await seedSubscriber("undo", asOf);
    // Failed send -> unclaim clears the bit and retires the lease.
    await runReminderPass(env, { asOf, send: async (to) => store.normalizeEmail(to) !== sub.email });
    expect(await remindersSent(sub.id)).toEqual([]);
    // Drive the REAL lost-edit path (AuditLab r3 LOW 2): an email-changing
    // edit (resetConfirmation) whose prior snapshot still held [7], then
    // restoreFirmLicenseAfterLostEdit() putting that prior back. The lease
    // join is s.cycle = rc.cycle, so this only recovers while neither SET
    // list writes `cycle` -- asserted below, not assumed.
    const firm = await store.createFirm(env.DB, { name: "sec96 undo firm", adminEmail: `sec96-undo-admin-${Date.now()}@example.com` });
    await env.DB.prepare("UPDATE subscribers SET firm_id = ?1 WHERE id = ?2").bind(firm.id, sub.id).run();
    const current = await env.DB.prepare("SELECT * FROM subscribers WHERE id = ?1").bind(sub.id).first<store.SubscriberRow>();
    if (!current) throw new Error("seeded subscriber vanished");
    const prior: store.SubscriberRow = { ...current, reminders_sent: "[7]" };
    const updated = await store.updateFirmLicense(env.DB, firm.id, sub.id, {
      email: `sec96-undo-new-${Date.now()}@example.com`,
      staffLabel: current.staff_label,
      stateSlug: current.state_slug,
      deadlineFields: JSON.parse(current.deadline_fields || "{}"),
      deadlineSource: current.deadline_source,
      userDeadline: current.user_deadline,
      resetConfirmation: true,
      renewalFeeCents: current.renewal_fee_cents,
      carryoverHours: current.carryover_hours,
      officeTag: current.office_tag,
      internalNotes: current.internal_notes,
      licenseIssueDate: current.license_issue_date,
    });
    if (!updated) throw new Error("updateFirmLicense returned null");
    expect(updated.cycle).toBe(current.cycle);
    expect(await store.restoreFirmLicenseAfterLostEdit(env.DB, firm.id, prior, updated)).toBe(true);
    const restored = await env.DB.prepare("SELECT cycle, status FROM subscribers WHERE id = ?1").bind(sub.id).first<{ cycle: number; status: string }>();
    expect(restored?.cycle).toBe(current.cycle);
    expect(await remindersSent(sub.id)).toEqual([7]);
    // Back to the consumer pass this suite exercises (the firm pass has its
    // own seat/plan gates that are out of scope here).
    await env.DB.prepare("UPDATE subscribers SET firm_id = NULL WHERE id = ?1").bind(sub.id).run();
    // Mutation control: with the lease row gone (the r2 DELETE), the tier
    // would read as completed and this pass would send nothing.
    const rec = recordingSend();
    await runReminderPass(env, { asOf, send: rec.send });
    expect(rec.sentTo.filter((e) => e === sub.email)).toHaveLength(1);
    expect(await remindersSent(sub.id)).toEqual([7]);
    expect((await leaseRows(sub.id))[0]?.completed_at).not.toBeNull();
  });

  it("escalation order (AuditLab #5 / SecurityLab 21:14Z): a stranded tier is skipped for good once a more urgent tier has been sent, and the recovery goes through nextDueThreshold", async () => {
    const { runReminderPass } = await import("../src/scheduler");
    const asOf = freshAsOf();
    const sub = await seedSubscriber("order", asOf);
    // Day 7 out: tier 7 is claimed, send throws, the unclaim is swallowed.
    vi.spyOn(store, "unclaimReminderThreshold").mockRejectedValueOnce(new Error("D1_ERROR: sec96 order strand"));
    await runReminderPass(env, {
      asOf,
      send: async (to) => {
        if (store.normalizeEmail(to) === sub.email) throw new Error("transport blew up");
        return true;
      },
    });
    vi.restoreAllMocks();
    expect(await remindersSent(sub.id)).toEqual([7]);
    // Customer snoozes past the tier-7 window (sch :412 skips before any
    // threshold work, so the lease is untouched while days tick down).
    const day1 = new Date(asOf.getTime() + 6 * MS_PER_DAY);
    await env.DB.prepare("UPDATE subscribers SET snoozed_until = ?1 WHERE id = ?2").bind(isoDaysAfter(asOf, 5), sub.id).run();
    await ageLeases(sub.id);
    // 1 day left: the stranded 7 is offered to nextDueThreshold() as unsent,
    // and it picks the tier that matches the real days remaining -- 1, not 7.
    const d1 = recordingSend();
    await runReminderPass(env, { asOf: day1, send: d1.send });
    expect(d1.sentTo.filter((e) => e === sub.email)).toHaveLength(1);
    expect(await remindersSent(sub.id)).toEqual([7, 1]);
    // Deadline day: 7 is still an open, stale lease, but tier 1 is more
    // urgent and sent, so :287 skips it. Nothing goes out, in either order.
    const day0 = new Date(asOf.getTime() + 7 * MS_PER_DAY);
    await ageLeases(sub.id);
    const d0 = recordingSend();
    await runReminderPass(env, { asOf: day0, send: d0.send });
    expect(d0.sentTo.filter((e) => e === sub.email)).toHaveLength(0);
    expect(await remindersSent(sub.id)).toEqual([7, 1]);
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

// ---------------------------------------------------------------------------
// SEC-100 (SecurityLab 2026-11-14, AuditLab 23:09Z): r3 fed the stale-adjusted
// view to nextDueThreshold()'s ordering guard, so dropping a stranded tier
// raised or nulled Math.min(alreadySent) and a LESS urgent tier could go out.
// Necessary and sufficient condition (AuditLab): min(reminders_sent) is itself
// stranded. These tests anchor on that orientation, including stock settings.
// ---------------------------------------------------------------------------

const ALL_TIERS = [1, 3, 7, 14, 30, 60];

function subsets<T>(xs: T[]): T[][] {
  const out: T[][] = [];
  for (let m = 0; m < 1 << xs.length; m++) out.push(xs.filter((_, i) => m & (1 << i)));
  return out;
}

// Pre-r3 behaviour, same shape as a19115e72: the raw column fed both
// neverNotified and the guard; catch-up was Math.min(thresholds), no order check.
function preR3Pick(d: number, raw: number[], thresholds: number[]): number | "none" | "grace" {
  if (d < -3) {
    if (raw.length === 0 && d >= -14) return Math.min(...thresholds);
    return "grace";
  }
  const mostUrgent = raw.length > 0 ? Math.min(...raw) : null;
  for (const t of [...thresholds].sort((a, b) => a - b)) {
    if (raw.includes(t)) continue;
    if (mostUrgent !== null && t >= mostUrgent) continue;
    if (d <= t) return t;
  }
  return "none";
}

describe("SEC-100 -- selectEmailReminderTier: ordering guard reads the raw column", () => {
  it("exhaustive: no out-of-order send in any (cadence x reminders_sent x stranded subset x day) state; stranded tiers still recover; no-strand states are identical to pre-r3", async () => {
    const { selectEmailReminderTier } = await import("../src/scheduler");
    const cadences = subsets(ALL_TIERS).filter((c) => c.length > 0);
    let states = 0;
    let recoveries = 0;
    let r3OutOfOrder = 0;
    let defaultCadenceR3OutOfOrder = 0;
    const violations: string[] = [];
    for (const cadence of cadences) {
      for (const raw of subsets(ALL_TIERS)) {
        for (const stale of subsets(raw)) {
          const view = raw.filter((t) => !stale.includes(t));
          const minRaw = raw.length > 0 ? Math.min(...raw) : Infinity;
          for (let d = -25; d <= 61; d++) {
            states += 1;
            const got = selectEmailReminderTier(d, view, raw, cadence);
            if (typeof got === "number") {
              if (got > minRaw || view.includes(got) || !cadence.includes(got)) {
                if (violations.length < 5) violations.push(`d=${d} raw=${raw} stale=${stale} cadence=${cadence} -> ${got}`);
              }
              if (stale.includes(got)) recoveries += 1;
            }
            if (stale.length === 0) {
              const old = preR3Pick(d, raw, cadence);
              if (old !== got && violations.length < 5) violations.push(`drift d=${d} raw=${raw} cadence=${cadence}: ${old} -> ${got}`);
            }
            // Positive control: r3 as built = the view fed to both.
            const bad = preR3Pick(d, view, cadence);
            if (typeof bad === "number" && bad > minRaw) {
              r3OutOfOrder += 1;
              if (cadence.length === 6) defaultCadenceR3OutOfOrder += 1;
            }
          }
        }
      }
    }
    expect(violations).toEqual([]);
    expect(states).toBe(63 * 729 * 87);
    // Includes the stranded-most-urgent-tier re-sends a `>=` raw guard would
    // block (that guard would also kill the base SEC-96 recovery).
    expect(recoveries).toBe(77_194);
    // The r3 shape really is out of order on this sweep. Both counts match
    // AuditLab's independent Python model of the same space (23:09Z).
    expect(r3OutOfOrder).toBe(251_948);
    expect(defaultCadenceR3OutOfOrder).toBe(5_154);
  }, 120_000);

  it("stock-settings minimal case (AuditLab): d=2, reminders_sent=[1], stale={1}, default cadence sends nothing, not tier 3; d=1 re-sends tier 1", async () => {
    const { selectEmailReminderTier } = await import("../src/scheduler");
    expect(selectEmailReminderTier(2, [], [1], ALL_TIERS)).toBe("none");
    expect(selectEmailReminderTier(1, [], [1], ALL_TIERS)).toBe(1);
    // Route 2: narrowed cadence past the deadline -> no send (logged as a drop).
    expect(selectEmailReminderTier(-8, [], [1], [3, 7])).toBe("grace");
    // Route 2, in-order recovery: stranded 7, cadence still has 1 -> tier 1.
    expect(selectEmailReminderTier(-8, [], [7], [1, 3, 7])).toBe(1);
    // Never notified at all: unchanged catch-up at the cadence's most urgent tier.
    expect(selectEmailReminderTier(-8, [], [], [3, 7])).toBe(3);
  });
});

async function strand(id: string, tier: number): Promise<void> {
  expect(await store.claimReminderThreshold(env.DB, id, "[]", tier)).toBe(true);
  await ageLeases(id);
}

for (const pass of ["reminder", "digest"] as const) {
  describe(`SEC-100 -- ${pass} pass: the most urgent sent tier is the stranded one`, () => {
    const mode = pass === "digest" ? store.NOTIFICATION_MODE_DIGEST : null;
    async function run(asOf: Date, send: (to: string, built: BuiltEmail) => Promise<boolean>) {
      const sch = await import("../src/scheduler");
      return pass === "digest" ? sch.runDigestPass(env, { asOf, send }) : sch.runReminderPass(env, { asOf, send });
    }

    it("route 1, default cadence: stranded tier 1 at 2 days out sends nothing (not tier 3); at 1 day out tier 1 is re-sent", async () => {
      const asOf = freshAsOf();
      const sub = await seedSubscriber(`s100r1${pass}`, asOf, 2, mode);
      await strand(sub.id, 1);
      const d2 = recordingSend();
      await run(asOf, d2.send);
      expect(d2.sentTo.filter((e) => e === sub.email)).toHaveLength(0);
      expect(await remindersSent(sub.id)).toEqual([1]);
      await ageLeases(sub.id);
      const d1 = recordingSend();
      await run(new Date(asOf.getTime() + MS_PER_DAY), d1.send);
      expect(d1.sentTo.filter((e) => e === sub.email)).toHaveLength(1);
      expect(await remindersSent(sub.id)).toEqual([1]);
      expect((await leaseRows(sub.id)).find((r) => r.threshold_days === 1)?.completed_at).not.toBeNull();
    });

    it("route 2 (past-deadline catch-up), narrowed cadence: stranded tier 1 sends nothing and logs the drop; an empty column still gets the catch-up", async () => {
      const asOf = freshAsOf();
      const sub = await seedSubscriber(`s100r2${pass}`, asOf, -8, mode);
      const ctl = await seedSubscriber(`s100r2ctl${pass}`, asOf, -8, mode);
      for (const id of [sub.id, ctl.id]) {
        await env.DB.prepare("UPDATE subscribers SET reminder_thresholds = '[3,7]' WHERE id = ?1").bind(id).run();
      }
      await strand(sub.id, 1);
      const rec = recordingSend();
      const summary = await run(asOf, rec.send);
      expect(rec.sentTo.filter((e) => e === sub.email)).toHaveLength(0);
      expect(await remindersSent(sub.id)).toEqual([1]);
      expect(summary.skipped_grace_period).toBeGreaterThan(0);
      // Control: never notified -> catch-up at the cadence's most urgent tier.
      expect(rec.sentTo.filter((e) => e === ctl.email)).toHaveLength(1);
      expect(await remindersSent(ctl.id)).toEqual([3]);
    });
  });
}

describe("SecurityLab r3 item 2 -- digest undo asymmetry", () => {
  it("an advanceDigestWindow throw after a real send does not strip the sent tier, and the next pass does not re-send", async () => {
    const { runDigestPass } = await import("../src/scheduler");
    const asOf = freshAsOf();
    const sub = await seedSubscriber("digestundo", asOf, 2, store.NOTIFICATION_MODE_DIGEST);
    const advSpy = vi.spyOn(store, "advanceDigestWindow").mockRejectedValueOnce(new Error("D1_ERROR: sec100 advance"));
    const first = recordingSend();
    await runDigestPass(env, { asOf, send: first.send });
    expect(advSpy.mock.calls.length).toBeGreaterThan(0);
    expect(first.sentTo.filter((e) => e === sub.email)).toHaveLength(1);
    expect(await remindersSent(sub.id)).toEqual([3]);
    expect((await leaseRows(sub.id)).find((r) => r.threshold_days === 3)?.completed_at).not.toBeNull();
    advSpy.mockRestore();
    await ageLeases(sub.id);
    const second = recordingSend();
    await runDigestPass(env, { asOf, send: second.send });
    expect(second.sentTo.filter((e) => e === sub.email)).toHaveLength(0);
  });
});
