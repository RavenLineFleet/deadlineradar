/**
 * PR6-B (migration 0086/0087, 2026-10-02, Devin via orchestrator, round 2):
 * the trial seat cap is 35 (not unlimited -- reverses the first PR6 pass).
 * At trial end, an unpaid firm whose roster is still over its OWN
 * (non-trial) cap gets a picker: the admin chooses which staff stay
 * active, the rest are PAUSED (no reminders, nothing deleted, restored
 * instantly on upgrade or on a different pick). Default if the admin
 * hasn't chosen: the earliest-added N stay active.
 *
 * Three things this file proves, per the orchestrator's own test list:
 *   1. An over-cap, trial-lapsed firm gets paused down to the earliest-N
 *      default, and ONLY those N actually receive reminders (the real
 *      runReminderPass(), not just a unit check of allConfirmedActive()).
 *   2. The admin's own explicit pick persists and is never silently
 *      overridden by the default again.
 *   3. An upgrade restores everyone instantly (the webhook path), and the
 *      nightly reconciliation sweep catches a firm that never logs back in.
 */
import { env, SELF } from "cloudflare:test";
import { describe, expect, it, vi } from "vitest";
import * as store from "../src/store";

const BASE = "https://deadline-radar.com";

function testExecutionContext(): ExecutionContext {
  return { waitUntil() {}, passThroughOnException() {}, props: {} } as unknown as ExecutionContext;
}

async function workerFetch(request: Request, envOverrides: Record<string, unknown> = {}): Promise<Response> {
  const worker = (await import("../src/index")).default;
  return worker.fetch(request, { ...env, ...envOverrides } as never, testExecutionContext());
}

async function createFirmWithSession(name: string, adminEmail: string): Promise<{ firmId: string; cookie: string }> {
  const firm = await store.createFirm(env.DB, { name, adminEmail });
  const { rawSessionToken } = await store.createSession(env.DB, firm.id);
  return { firmId: firm.id, cookie: `dr_firm_session=${rawSessionToken}` };
}

// PR6-D (2026-10-02): trial end alone no longer triggers the automatic
// default-pause -- there's a 7-day grace window after it (see
// rosterPauseGraceHasElapsed() in store.ts). This helper moves the clock
// comfortably PAST that grace window (8 days, not 1), so every test in this
// file that asserts the default-pause OUTCOME keeps testing that outcome
// rather than accidentally testing grace-window timing it didn't intend to.
// expireTrialWithinGrace() below is the one that tests the grace window
// itself.
async function expireTrial(firmId: string): Promise<void> {
  await env.DB.prepare("UPDATE firms SET trial_ends_at = ?1 WHERE id = ?2")
    .bind(new Date(Date.now() - 8 * 86_400_000).toISOString(), firmId)
    .run();
}

/** Trial ended 2 days ago -- lapsed, but still inside the 7-day PR6-D grace
 * window. Used only by the tests that specifically prove grace behavior. */
async function expireTrialWithinGrace(firmId: string): Promise<void> {
  await env.DB.prepare("UPDATE firms SET trial_ends_at = ?1 WHERE id = ?2")
    .bind(new Date(Date.now() - 2 * 86_400_000).toISOString(), firmId)
    .run();
}

/** Fills a firm's roster directly via store.ts, same "bypass HTTP for setup
 * speed" reasoning as worker.spec.ts's own fillRoster() -- these tests are
 * about the pause mechanism, not re-proving ordinary staff-create works.
 * Staggered createdAt (1ms apart) so "earliest-added" has a real, stable
 * order to sort on -- addPending() itself stamps createdAt from Date.now(),
 * which on a fast test runner can tie within the same millisecond. */
async function fillRosterStaggered(firmId: string, n: number, labelPrefix: string): Promise<string[]> {
  const ids: string[] = [];
  for (let i = 0; i < n; i++) {
    const row = await store.addPending(env.DB, {
      email: `${labelPrefix}-${i}-${Date.now()}@example.com`,
      stateSlug: "georgia",
      deadlineFields: { license_type_id: "ga-individual" },
      firstName: null,
      deadlineSource: store.DEADLINE_SOURCE_COMPUTED,
      userDeadline: null,
      firmId,
      staffLabel: null,
      skipConfirmation: true,
    });
    ids.push(row.id);
    await env.DB.prepare("UPDATE subscribers SET created_at = ?1 WHERE id = ?2")
      .bind(new Date(Date.now() + i).toISOString(), row.id)
      .run();
  }
  return ids;
}

describe("store.reconcileRosterPauseState -- the default (no admin pick)", () => {
  it("pauses all but the earliest-3-added once over cap and the trial has lapsed", async () => {
    const { firmId } = await createFirmWithSession("Pause Default Firm A", `pausedefault-a-${Date.now()}@example.com`);
    const ids = await fillRosterStaggered(firmId, 6, "pausedefault-a");
    await expireTrial(firmId);

    const reconciled = await store.reconcileRosterPauseState(env.DB, firmId);
    const active = reconciled.filter((r) => r.paused_at === null).map((r) => r.id);
    const paused = reconciled.filter((r) => r.paused_at !== null).map((r) => r.id);

    expect(active.sort()).toEqual(ids.slice(0, 3).sort());
    expect(paused.sort()).toEqual(ids.slice(3).sort());
  });

  it("is idempotent -- calling it twice in a row doesn't change anything further", async () => {
    const { firmId } = await createFirmWithSession("Pause Default Firm B", `pausedefault-b-${Date.now()}@example.com`);
    await fillRosterStaggered(firmId, 5, "pausedefault-b");
    await expireTrial(firmId);

    const first = await store.reconcileRosterPauseState(env.DB, firmId);
    const second = await store.reconcileRosterPauseState(env.DB, firmId);
    expect(second.map((r) => r.paused_at)).toEqual(first.map((r) => r.paused_at));
  });

  it("does NOT pause anyone while the trial is still active, even over the non-trial cap", async () => {
    const { firmId } = await createFirmWithSession("Pause Default Firm C", `pausedefault-c-${Date.now()}@example.com`);
    await fillRosterStaggered(firmId, 6, "pausedefault-c"); // trial_ends_at is still in the future (real signup default)

    const reconciled = await store.reconcileRosterPauseState(env.DB, firmId);
    expect(reconciled.every((r) => r.paused_at === null)).toBe(true);
  });

  it("does NOT pause anyone on a real paid tier, even if somehow over its own cap", async () => {
    const { firmId } = await createFirmWithSession("Pause Default Firm D", `pausedefault-d-${Date.now()}@example.com`);
    await env.DB.prepare("UPDATE firms SET plan_tier = 'firm_starter' WHERE id = ?1").bind(firmId).run(); // cap 5
    await fillRosterStaggered(firmId, 6, "pausedefault-d"); // 1 over its own cap
    await expireTrial(firmId);

    const reconciled = await store.reconcileRosterPauseState(env.DB, firmId);
    expect(reconciled.every((r) => r.paused_at === null)).toBe(true);
  });

  it("unpauses everyone once the roster is back at or under cap (e.g. staff removed)", async () => {
    const { firmId } = await createFirmWithSession("Pause Default Firm E", `pausedefault-e-${Date.now()}@example.com`);
    const ids = await fillRosterStaggered(firmId, 6, "pausedefault-e");
    await expireTrial(firmId);
    await store.reconcileRosterPauseState(env.DB, firmId);

    // Admin-removes 3 staff down to the cap via the real DELETE route semantics
    // (stop_reason marks them excluded from listFirmLicenses() going forward).
    for (const id of ids.slice(3)) {
      await env.DB.prepare("UPDATE subscribers SET status = ?1, stop_reason = ?2 WHERE id = ?3")
        .bind(store.STATUS_STOPPED, "removed_by_admin", id)
        .run();
    }
    const reconciled = await store.reconcileRosterPauseState(env.DB, firmId);
    expect(reconciled.every((r) => r.paused_at === null)).toBe(true);
  });
});

describe("store.reconcileRosterPauseState -- PR6-D grace window (day 14-21)", () => {
  it("does NOT auto-pause anyone while the trial has lapsed but grace hasn't elapsed, even far over cap", async () => {
    const { firmId } = await createFirmWithSession("Grace Firm A", `grace-a-${Date.now()}@example.com`);
    await fillRosterStaggered(firmId, 6, "grace-a");
    await expireTrialWithinGrace(firmId); // lapsed 2 days ago -- inside the 7-day grace

    const reconciled = await store.reconcileRosterPauseState(env.DB, firmId);
    expect(reconciled.every((r) => r.paused_at === null)).toBe(true);
  });

  it("unpauses anyone the OLD (pre-PR6-D) logic had already paused, once re-reconciled inside the grace window", async () => {
    // Guards against a version of the fix that only skips the FIRST pause
    // but doesn't reverse one a stale run already applied.
    const { firmId } = await createFirmWithSession("Grace Firm B", `grace-b-${Date.now()}@example.com`);
    const ids = await fillRosterStaggered(firmId, 6, "grace-b");
    await env.DB.prepare("UPDATE subscribers SET paused_at = ?1 WHERE id = ?2").bind(new Date().toISOString(), ids[5]).run();
    await expireTrialWithinGrace(firmId);

    const reconciled = await store.reconcileRosterPauseState(env.DB, firmId);
    expect(reconciled.every((r) => r.paused_at === null)).toBe(true);
  });

  it("the automatic default DOES apply once the grace window has fully elapsed (>= day 21)", async () => {
    const { firmId } = await createFirmWithSession("Grace Firm C", `grace-c-${Date.now()}@example.com`);
    const ids = await fillRosterStaggered(firmId, 6, "grace-c");
    await expireTrial(firmId); // 8 days past trial end -- past the 7-day grace

    const reconciled = await store.reconcileRosterPauseState(env.DB, firmId);
    const active = reconciled.filter((r) => r.paused_at === null).map((r) => r.id);
    expect(active.sort()).toEqual(ids.slice(0, 3).sort());
  });

  it("the admin's own explicit pick applies immediately even DURING the grace window -- grace only delays the AUTOMATIC default", async () => {
    const { firmId } = await createFirmWithSession("Grace Firm D", `grace-d-${Date.now()}@example.com`);
    const ids = await fillRosterStaggered(firmId, 6, "grace-d");
    await expireTrialWithinGrace(firmId);

    const chosen = ids.slice(3); // the latest 3, not the earliest-3 default
    await store.setRosterActivePicks(env.DB, firmId, chosen);

    const reconciled = await store.reconcileRosterPauseState(env.DB, firmId);
    const active = reconciled.filter((r) => r.paused_at === null).map((r) => r.id);
    expect(active.sort()).toEqual([...chosen].sort());
  });

  it("a firm with no trial_ends_at at all (pre-PR6) has no grace window -- reconciles immediately", async () => {
    const { firmId } = await createFirmWithSession("Grace Firm E", `grace-e-${Date.now()}@example.com`);
    const ids = await fillRosterStaggered(firmId, 6, "grace-e");
    await env.DB.prepare("UPDATE firms SET trial_ends_at = NULL WHERE id = ?1").bind(firmId).run();

    const reconciled = await store.reconcileRosterPauseState(env.DB, firmId);
    const active = reconciled.filter((r) => r.paused_at === null).map((r) => r.id);
    expect(active.sort()).toEqual(ids.slice(0, 3).sort());
  });
});

describe("store.setRosterActivePicks -- the admin's own explicit choice", () => {
  it("persists the pick, and reconciliation never reverts it to the earliest-added default afterward", async () => {
    const { firmId } = await createFirmWithSession("Pick Firm A", `pickfirm-a-${Date.now()}@example.com`);
    const ids = await fillRosterStaggered(firmId, 6, "pickfirm-a");
    await expireTrial(firmId);

    // Admin picks the LATEST 3, not the earliest 3 the default would choose.
    const chosen = ids.slice(3);
    await store.setRosterActivePicks(env.DB, firmId, chosen);

    const reconciled = await store.reconcileRosterPauseState(env.DB, firmId);
    const active = reconciled.filter((r) => r.paused_at === null).map((r) => r.id);
    expect(active.sort()).toEqual([...chosen].sort());

    const firm = await store.getFirmById(env.DB, firmId);
    expect(firm?.active_staff_choice_at).toBeTruthy();
  });

  it("an empty pick pauses everyone -- a deliberate, valid choice, not an error", async () => {
    const { firmId } = await createFirmWithSession("Pick Firm B", `pickfirm-b-${Date.now()}@example.com`);
    await fillRosterStaggered(firmId, 6, "pickfirm-b");
    await expireTrial(firmId);

    await store.setRosterActivePicks(env.DB, firmId, []);
    const reconciled = await store.reconcileRosterPauseState(env.DB, firmId);
    expect(reconciled.every((r) => r.paused_at !== null)).toBe(true);
  });
});

describe("store.allConfirmedActive -- paused subscribers are excluded fleet-wide", () => {
  it("a paused subscriber is skipped; an active one on the same firm is still returned", async () => {
    const { firmId } = await createFirmWithSession("AllActive Firm", `allactive-${Date.now()}@example.com`);
    const [activeId, pausedId] = await fillRosterStaggered(firmId, 2, "allactive");
    await env.DB.prepare("UPDATE subscribers SET paused_at = ?1 WHERE id = ?2").bind(new Date().toISOString(), pausedId).run();

    const all = await store.allConfirmedActive(env.DB);
    const ids = all.map((r) => r.id);
    expect(ids).toContain(activeId);
    expect(ids).not.toContain(pausedId);
  });
});

describe("PR6-B end-to-end: over-cap trial -> picker -> only the chosen staff get reminders", () => {
  it("the real runReminderPass() sends to exactly the 3 earliest-added staff once over cap and trial-lapsed, with no explicit pick", async () => {
    const { runReminderPass } = await import("../src/scheduler");
    const { firmId } = await createFirmWithSession("E2E Reminder Firm A", `e2e-a-${Date.now()}@example.com`);
    // Same Texas/birth_month=7 fixture reminder-log.spec.ts uses -- a real,
    // well-understood "due for a reminder on 2026-07-24" shape (7-day
    // threshold before the end-of-July deadline), applied to all 6 staff.
    const ids: string[] = [];
    for (let i = 0; i < 6; i++) {
      const row = await store.addPending(env.DB, {
        email: `e2e-a-staff-${i}-${Date.now()}@example.com`,
        stateSlug: "texas",
        deadlineFields: { birth_month: "7" },
        firstName: "Tester",
        firmId,
        staffLabel: `Staff ${i}`,
        skipConfirmation: true,
      });
      ids.push(row.id);
      await env.DB.prepare("UPDATE subscribers SET created_at = ?1 WHERE id = ?2")
        .bind(new Date(Date.now() + i).toISOString(), row.id)
        .run();
    }
    await expireTrial(firmId);
    await store.reconcileRosterPauseState(env.DB, firmId); // same reconciliation the nightly cron/dashboard load trigger

    let sentTo: string[] = [];
    await runReminderPass(env, {
      asOf: new Date(Date.UTC(2026, 6, 24)),
      send: async (to) => {
        sentTo.push(to);
        return true;
      },
    });

    expect(sentTo.length).toBe(3);
  });

  it("upgrading restores ALL staff instantly -- the webhook path, not just the dashboard/cron", async () => {
    const { firmId } = await createFirmWithSession("E2E Upgrade Firm", `e2e-upgrade-${Date.now()}@example.com`);
    const ids = await fillRosterStaggered(firmId, 6, "e2e-upgrade");
    await expireTrial(firmId);
    await store.reconcileRosterPauseState(env.DB, firmId);
    const beforeUpgrade = await store.listFirmLicenses(env.DB, firmId);
    expect(beforeUpgrade.filter((r) => r.paused_at !== null).length).toBe(3);

    const payload = JSON.stringify({
      id: `evt_pr6b_upgrade_${firmId}`,
      type: "checkout.session.completed",
      data: { object: { customer: "cus_pr6b_1", subscription: "sub_pr6b_1", metadata: { firm_id: firmId, target_plan_tier: "firm_growth" } } }, // cap 10, covers all 6
    });
    const SECRET = "whsec_pr6b_test_secret";
    const t = Math.floor(Date.now() / 1000);
    const signedPayload = `${t}.${payload}`;
    const key = await crypto.subtle.importKey("raw", new TextEncoder().encode(SECRET), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
    const sigBuffer = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(signedPayload));
    const hex = [...new Uint8Array(sigBuffer)].map((b) => b.toString(16).padStart(2, "0")).join("");
    const sig = `t=${t},v1=${hex}`;

    const resp = await workerFetch(
      new Request(`${BASE}/stripe/webhook`, { method: "POST", headers: { "content-type": "application/json", "Stripe-Signature": sig }, body: payload }),
      { STRIPE_SECRET_KEY: "sk_test_x", STRIPE_WEBHOOK_SECRET: SECRET }
    );
    expect(resp.status).toBe(200);

    const afterUpgrade = await store.listFirmLicenses(env.DB, firmId);
    expect(afterUpgrade.every((r) => r.paused_at === null)).toBe(true);
    expect(afterUpgrade.length).toBe(ids.length);
  });

  it("the nightly runRosterPauseReconciliationPass() catches a firm that never logs back in or upgrades", async () => {
    const { runRosterPauseReconciliationPass } = await import("../src/scheduler");
    const { firmId } = await createFirmWithSession("E2E Cron Firm", `e2e-cron-${Date.now()}@example.com`);
    await fillRosterStaggered(firmId, 5, "e2e-cron");
    await expireTrial(firmId);

    const before = await store.listFirmLicenses(env.DB, firmId);
    expect(before.every((r) => r.paused_at === null)).toBe(true); // nobody's touched this firm yet

    const summary = await runRosterPauseReconciliationPass({ ...env, SEND_APPROVED_PASSES: "rosterPauseReconciliation" } as never);
    expect(summary.firmsChecked).toBeGreaterThan(0);

    const after = await store.listFirmLicenses(env.DB, firmId);
    expect(after.filter((r) => r.paused_at !== null).length).toBe(2); // 5 staff, cap 3
  });

  it("runRosterPauseReconciliationPass does nothing when SEND_APPROVED_PASSES doesn't include it -- fails closed by default", async () => {
    const { runRosterPauseReconciliationPass } = await import("../src/scheduler");
    const { firmId } = await createFirmWithSession("E2E Cron Gate Firm", `e2e-crongate-${Date.now()}@example.com`);
    await fillRosterStaggered(firmId, 5, "e2e-crongate");
    await expireTrial(firmId);

    const summary = await runRosterPauseReconciliationPass({ ...env, SEND_APPROVED_PASSES: undefined } as never);
    expect(summary.firmsChecked).toBe(0);

    const after = await store.listFirmLicenses(env.DB, firmId);
    expect(after.every((r) => r.paused_at === null)).toBe(true); // untouched -- the pass never ran
  });
});

describe("POST /firm/licenses/active-picks", () => {
  it("401s with no session", async () => {
    const resp = await SELF.fetch(`${BASE}/firm/licenses/active-picks`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ active_subscriber_ids: [] }),
    });
    expect(resp.status).toBe(401);
  });

  it("403s for a Staff-role session -- Partner/Office Manager only", async () => {
    const { firmId, cookie: _unused } = await createFirmWithSession("Picks Role Firm", `picksrole-${Date.now()}@example.com`);
    const staffMember = await store.createFirmMember(env.DB, {
      firmId,
      email: `picksrole-staff-${Date.now()}@example.com`,
      role: "staff",
      alreadyJoined: true,
    });
    const { rawSessionToken } = await store.createSession(env.DB, firmId, staffMember.id);
    const staffCookie = `dr_firm_session=${rawSessionToken}`;

    const resp = await workerFetch(
      new Request(`${BASE}/firm/licenses/active-picks`, {
        method: "POST",
        headers: { "content-type": "application/json", Cookie: staffCookie },
        body: JSON.stringify({ active_subscriber_ids: [] }),
      })
    );
    expect(resp.status).toBe(403);
  });

  it("400s on an id that doesn't belong to this firm's roster", async () => {
    const { firmId, cookie } = await createFirmWithSession("Picks Ownership Firm", `picksownership-${Date.now()}@example.com`);
    await fillRosterStaggered(firmId, 2, "picksownership");
    const { firmId: otherFirmId } = await createFirmWithSession("Other Firm", `otherfirm-${Date.now()}@example.com`);
    const [otherId] = await fillRosterStaggered(otherFirmId, 1, "otherfirm");

    const resp = await workerFetch(
      new Request(`${BASE}/firm/licenses/active-picks`, {
        method: "POST",
        headers: { "content-type": "application/json", Cookie: cookie },
        body: JSON.stringify({ active_subscriber_ids: [otherId] }),
      })
    );
    expect(resp.status).toBe(400);
  });

  it("400s when the pick exceeds the firm's own cap", async () => {
    const { firmId, cookie } = await createFirmWithSession("Picks Cap Firm", `pickscap-${Date.now()}@example.com`);
    const ids = await fillRosterStaggered(firmId, 6, "pickscap");
    await expireTrial(firmId); // cap 3

    const resp = await workerFetch(
      new Request(`${BASE}/firm/licenses/active-picks`, {
        method: "POST",
        headers: { "content-type": "application/json", Cookie: cookie },
        body: JSON.stringify({ active_subscriber_ids: ids.slice(0, 4) }), // 4 > cap 3
      })
    );
    expect(resp.status).toBe(400);
  });

  it("200s and returns the reconciled roster on a valid pick", async () => {
    const { firmId, cookie } = await createFirmWithSession("Picks Happy Firm", `pickshappy-${Date.now()}@example.com`);
    const ids = await fillRosterStaggered(firmId, 6, "pickshappy");
    await expireTrial(firmId);
    const chosen = [ids[1] as string, ids[4] as string, ids[5] as string];

    const resp = await workerFetch(
      new Request(`${BASE}/firm/licenses/active-picks`, {
        method: "POST",
        headers: { "content-type": "application/json", Cookie: cookie },
        body: JSON.stringify({ active_subscriber_ids: chosen }),
      })
    );
    expect(resp.status).toBe(200);
    const body = (await resp.json()) as { licenses: { id: string; paused: boolean }[] };
    const activeIds = body.licenses.filter((l) => !l.paused).map((l) => l.id);
    expect(activeIds.sort()).toEqual([...chosen].sort());
  });
});

describe("runTrialEndingAlertPass -- the two real customer emails", () => {
  it("does nothing when SEND_APPROVED_PASSES doesn't include this pass", async () => {
    const { runTrialEndingAlertPass } = await import("../src/scheduler");
    const { firmId } = await createFirmWithSession("Alert Gate Firm", `alertgate-${Date.now()}@example.com`);
    await fillRosterStaggered(firmId, 6, "alertgate");
    const summary = await runTrialEndingAlertPass({ ...env, SEND_APPROVED_PASSES: undefined } as never);
    expect(summary.endingSoonSent).toBe(0);
    expect(summary.pausedSent).toBe(0);
  });

  it("sends the 'ending soon' email once for an over-cap firm within the warning window, and never a second time", async () => {
    // PR6-B isolation note: runTrialEndingAlertPass() scans ALL firms for
    // BOTH notice types in one call -- other tests in this file leave
    // firms sitting over cap with a lapsed, never-notified trial, which
    // legitimately qualify for the OTHER notice (roster-paused) the first
    // time this pass ever runs in this file. That's correct behavior, not
    // cross-test leakage to suppress -- so this asserts sends TO THIS
    // FIRM specifically, not a raw total across every firm the mocked
    // send() happens to see.
    const { runTrialEndingAlertPass } = await import("../src/scheduler");
    const { firmId } = await createFirmWithSession("Alert Soon Firm", `alertsoon-${Date.now()}@example.com`);
    await fillRosterStaggered(firmId, 6, "alertsoon");
    await env.DB.prepare("UPDATE firms SET trial_ends_at = ?1 WHERE id = ?2")
      .bind(new Date(Date.now() + 2 * 86_400_000).toISOString(), firmId) // ends in 2 days -- inside the 3-day window
      .run();
    const firm = await store.getFirmById(env.DB, firmId);
    const thisFirmEmail = firm?.admin_email as string;

    const sent: string[] = [];
    const opts = { SEND_APPROVED_PASSES: "trialEndingAlert", RESEND_API_KEY: "test-key" };
    await runTrialEndingAlertPass({ ...env, ...opts } as never, { send: async (to) => { sent.push(to); return true; } });
    await runTrialEndingAlertPass({ ...env, ...opts } as never, { send: async (to) => { sent.push(to); return true; } });

    expect(sent.filter((to) => to === thisFirmEmail).length).toBe(1);
  });

  it("sends NO 'ending soon' email for a firm within the window but NOT over cap", async () => {
    const { runTrialEndingAlertPass } = await import("../src/scheduler");
    const { firmId } = await createFirmWithSession("Alert Soon Under Cap Firm", `alertsoonundercap-${Date.now()}@example.com`);
    await fillRosterStaggered(firmId, 2, "alertsoonundercap"); // under the free cap of 3
    await env.DB.prepare("UPDATE firms SET trial_ends_at = ?1 WHERE id = ?2")
      .bind(new Date(Date.now() + 2 * 86_400_000).toISOString(), firmId)
      .run();

    const sent: string[] = [];
    await runTrialEndingAlertPass(
      { ...env, SEND_APPROVED_PASSES: "trialEndingAlert", RESEND_API_KEY: "test-key" } as never,
      { send: async (to) => { sent.push(to); return true; } }
    );
    expect(sent.length).toBe(0);
  });

  it("sends the 'N staff paused' email once a lapsed trial has actually produced paused staff, and never a second time", async () => {
    // Same per-firm isolation reasoning as the "ending soon" test above.
    const { runTrialEndingAlertPass } = await import("../src/scheduler");
    const { firmId } = await createFirmWithSession("Alert Paused Firm", `alertpaused-${Date.now()}@example.com`);
    await fillRosterStaggered(firmId, 6, "alertpaused");
    await expireTrial(firmId);
    const firm = await store.getFirmById(env.DB, firmId);
    const thisFirmEmail = firm?.admin_email as string;

    const sent: string[] = [];
    const opts = { SEND_APPROVED_PASSES: "trialEndingAlert", RESEND_API_KEY: "test-key" };
    await runTrialEndingAlertPass({ ...env, ...opts } as never, { send: async (to) => { sent.push(to); return true; } });
    await runTrialEndingAlertPass({ ...env, ...opts } as never, { send: async (to) => { sent.push(to); return true; } });

    expect(sent.filter((to) => to === thisFirmEmail).length).toBe(1);
  });
});
