/**
 * Roadmap #9/#319 (2026-08-08): proactive rule-change email alerts, scoped
 * to a firm's own roster states. Modeled on the reminder pass's own test
 * shape (worker.spec.ts's "scheduler.ts runReminderPass -- one pass") for
 * the cron pass, plus a PATCH-route describe block matching the existing
 * "POST /firm/rule-change/notify" conventions.
 */
import { env, SELF } from "cloudflare:test";
import { describe, expect, it, vi } from "vitest";
import * as store from "../src/store";
import { runRuleChangeAlertPass } from "../src/scheduler";

const BASE = "https://deadline-radar.com";

async function testExecutionContext(): Promise<ExecutionContext> {
  return { waitUntil() {}, passThroughOnException() {}, props: {} } as unknown as ExecutionContext;
}

async function workerFetch(request: Request, envOverrides: Record<string, unknown> = {}): Promise<Response> {
  const worker = (await import("../src/index")).default;
  return worker.fetch(request, { ...env, ...envOverrides } as never, await testExecutionContext());
}

async function newFirmWithRosterLicense(label: string, stateSlug: string): Promise<{ firmId: string; memberId: string; adminEmail: string }> {
  const adminEmail = `${label}-${Date.now()}-${Math.floor(performance.now())}@examplefirm.com`;
  const { id: firmId, memberId } = await store.createFirm(env.DB, { name: `${label} LLP`, adminEmail });
  await env.DB.prepare(
    `INSERT INTO subscribers (id, email, cooldown_key, state_slug, deadline_fields, status, confirm_token, unsubscribe_token, renewed_token, created_at, confirmed_at, firm_id)
     VALUES (?1, ?2, ?2, ?3, '{}', 'confirmed', ?4, ?5, ?6, datetime('now'), datetime('now'), ?7)`
  )
    .bind(store.newToken(), `staff-${label}-${Date.now()}@example.com`, stateSlug, store.newToken(), store.newToken(), store.newToken(), firmId)
    .run();
  return { firmId, memberId, adminEmail };
}

async function sessionCookieFor(firmId: string, memberId: string): Promise<string> {
  const { rawSessionToken } = await store.createSession(env.DB, firmId, memberId);
  return `dr_firm_session=${rawSessionToken}`;
}

// AuditLab REGEN-8 (2026-08-26): the previous reference here
// (colorado-mobility-2026-08-12) went stale once REGEN-3's status-fallback
// fix correctly withheld it (missing status field), breaking this whole
// describe block -- a real, dated event id is inherently a moving target.
// Picked the live event with the FURTHEST-OUT effective_date at time of
// writing (2026-11-01) to buy the most runway before this needs updating
// again; there is no synthetic-event injection path in
// runRuleChangeAlertPass() to test against instead (send() is the only
// override RunReminderOptions exposes).
const REAL_EVENT_ID = "oklahoma-regwatch-ff8bf9c424be";
const REAL_EVENT_STATE = "oklahoma";

describe("store.findFirmsEligibleForRuleChangeAlert()", () => {
  it("includes a firm with alerts enabled and an active roster license in the event's state", async () => {
    const { firmId } = await newFirmWithRosterLicense("eligible-basic", REAL_EVENT_STATE);
    const firms = await store.findFirmsEligibleForRuleChangeAlert(env.DB, REAL_EVENT_STATE, REAL_EVENT_ID + "-basic", 500);
    expect(firms.some((f) => f.id === firmId)).toBe(true);
  });

  it("excludes a firm with alerts disabled", async () => {
    const { firmId } = await newFirmWithRosterLicense("eligible-disabled", REAL_EVENT_STATE);
    await store.setFirmRuleChangeAlertsEnabled(env.DB, firmId, false);
    const firms = await store.findFirmsEligibleForRuleChangeAlert(env.DB, REAL_EVENT_STATE, REAL_EVENT_ID + "-disabled", 500);
    expect(firms.some((f) => f.id === firmId)).toBe(false);
  });

  it("excludes a firm with no roster license in the event's state", async () => {
    const { firmId } = await newFirmWithRosterLicense("eligible-wrong-state", "texas");
    const firms = await store.findFirmsEligibleForRuleChangeAlert(env.DB, REAL_EVENT_STATE, REAL_EVENT_ID + "-wrongstate", 500);
    expect(firms.some((f) => f.id === firmId)).toBe(false);
  });

  it("excludes a firm whose only license in that state is opted out", async () => {
    const { firmId } = await newFirmWithRosterLicense("eligible-opted-out", REAL_EVENT_STATE);
    const row = await env.DB.prepare("SELECT unsubscribe_token FROM subscribers WHERE firm_id = ?1").bind(firmId).first<{ unsubscribe_token: string }>();
    await store.stop(env.DB, row!.unsubscribe_token, "unsubscribed");
    const firms = await store.findFirmsEligibleForRuleChangeAlert(env.DB, REAL_EVENT_STATE, REAL_EVENT_ID + "-optedout", 500);
    expect(firms.some((f) => f.id === firmId)).toBe(false);
  });

  it("excludes a firm already notified about that exact event_id", async () => {
    const { firmId } = await newFirmWithRosterLicense("eligible-already-notified", REAL_EVENT_STATE);
    const eventId = REAL_EVENT_ID + "-alreadynotified";
    await store.claimRuleChangeNotification(env.DB, firmId, eventId);
    const firms = await store.findFirmsEligibleForRuleChangeAlert(env.DB, REAL_EVENT_STATE, eventId, 500);
    expect(firms.some((f) => f.id === firmId)).toBe(false);
  });

  it("a firm not yet notified about a DIFFERENT event stays eligible", async () => {
    const { firmId } = await newFirmWithRosterLicense("eligible-different-event", REAL_EVENT_STATE);
    await store.claimRuleChangeNotification(env.DB, firmId, REAL_EVENT_ID + "-eventA");
    const firms = await store.findFirmsEligibleForRuleChangeAlert(env.DB, REAL_EVENT_STATE, REAL_EVENT_ID + "-eventB", 500);
    expect(firms.some((f) => f.id === firmId)).toBe(true);
  });

  it("excludes a non-active (suspended) firm", async () => {
    const { firmId } = await newFirmWithRosterLicense("eligible-suspended", REAL_EVENT_STATE);
    await env.DB.prepare("UPDATE firms SET status = 'suspended' WHERE id = ?1").bind(firmId).run();
    const firms = await store.findFirmsEligibleForRuleChangeAlert(env.DB, REAL_EVENT_STATE, REAL_EVENT_ID + "-suspended", 500);
    expect(firms.some((f) => f.id === firmId)).toBe(false);
  });
});

describe("store.claimRuleChangeNotification() / unclaim", () => {
  it("claim/unclaim dedup prevents a double-send under a simulated concurrent pass", async () => {
    const { firmId } = await newFirmWithRosterLicense("claim-race", REAL_EVENT_STATE);
    const eventId = REAL_EVENT_ID + "-race";
    const claimA = await store.claimRuleChangeNotification(env.DB, firmId, eventId);
    const claimB = await store.claimRuleChangeNotification(env.DB, firmId, eventId);
    expect(claimA).toBe(true);
    expect(claimB).toBe(false);

    await store.unclaimRuleChangeNotification(env.DB, firmId, eventId);
    const row = await env.DB.prepare("SELECT * FROM firm_rule_change_notifications WHERE firm_id = ?1 AND event_id = ?2")
      .bind(firmId, eventId)
      .first();
    expect(row).toBeNull();
  });
});

describe("runRuleChangeAlertPass() -- end to end", () => {
  it("sends exactly one alert to the firm admin for a real live event, and claims it", async () => {
    const { firmId, adminEmail } = await newFirmWithRosterLicense("e2e-basic", REAL_EVENT_STATE);
    let capturedTo = "";
    let capturedSubject = "";
    const summary = await runRuleChangeAlertPass(env, {
      send: async (to, built) => {
        if (to === adminEmail) {
          capturedTo = to;
          capturedSubject = built.subject;
        }
        return true;
      },
    });
    expect(summary.sent).toBeGreaterThan(0);
    expect(capturedTo).toBe(adminEmail);
    expect(capturedSubject).toMatch(/rule change/i);

    const row = await env.DB.prepare("SELECT * FROM firm_rule_change_notifications WHERE firm_id = ?1 AND event_id = ?2")
      .bind(firmId, REAL_EVENT_ID)
      .first();
    expect(row).not.toBeNull();
  });

  it("AuditLab LINK-1 (2026-08-10): account-settings link is an absolute URL even with STATIC_SITE_BASE_URL unset (real production shape)", async () => {
    const { adminEmail } = await newFirmWithRosterLicense("e2e-link1-absolute", REAL_EVENT_STATE);
    let capturedHtml = "";
    await runRuleChangeAlertPass(env, {
      send: async (to, built) => {
        if (to === adminEmail) capturedHtml = built.htmlBody;
        return true;
      },
    });
    expect(capturedHtml).not.toBe("");
    const hrefs = [...capturedHtml.matchAll(/href="([^"]+)"/g)].map((m) => m[1]);
    expect(hrefs.length).toBeGreaterThan(0);
    for (const href of hrefs) {
      expect(href).toMatch(/^https:\/\//);
    }
  });

  it("AuditLab UNSUB-2 (2026-08-10): carries a real one-click List-Unsubscribe header, and the link actually turns rule-change alerts off", async () => {
    const { firmId, adminEmail } = await newFirmWithRosterLicense("e2e-unsub2", REAL_EVENT_STATE);
    let capturedHeaders: Record<string, string> = {};
    await runRuleChangeAlertPass(env, {
      send: async (to, built) => {
        if (to === adminEmail) capturedHeaders = built.headers;
        return true;
      },
    });

    expect(capturedHeaders["List-Unsubscribe-Post"]).toBe("List-Unsubscribe=One-Click");
    const match = /<(https:\/\/[^>]+)>/.exec(capturedHeaders["List-Unsubscribe"] ?? "");
    expect(match).not.toBeNull();
    const unsubUrl = match![1]!;
    expect(unsubUrl).toContain("/firm-admin-unsubscribe/rule-change?token=");

    const resp = await SELF.fetch(unsubUrl, {
      method: "POST",
      headers: { "content-type": "application/x-www-form-urlencoded", "cf-connecting-ip": "203.0.113.96" },
      body: "List-Unsubscribe=One-Click",
    });
    expect(resp.status).toBe(200);

    const firm = await store.getFirmById(env.DB, firmId);
    expect(firm?.rule_change_alerts_enabled).toBe(0);
  });

  it("does not re-send on a second pass for the same firm/event", async () => {
    const { adminEmail } = await newFirmWithRosterLicense("e2e-no-resend", REAL_EVENT_STATE);
    await runRuleChangeAlertPass(env, { send: async () => true });

    let sentAgain = false;
    await runRuleChangeAlertPass(env, {
      send: async (to) => {
        if (to === adminEmail) sentAgain = true;
        return true;
      },
    });
    expect(sentAgain).toBe(false);
  });

  it("a failed send() reverts the claim so it retries next pass", async () => {
    const { firmId, adminEmail } = await newFirmWithRosterLicense("e2e-failed-send", REAL_EVENT_STATE);
    const summary = await runRuleChangeAlertPass(env, { send: async () => false });
    expect(summary.sent).toBe(0);

    const row = await env.DB.prepare("SELECT * FROM firm_rule_change_notifications WHERE firm_id = ?1 AND event_id = ?2")
      .bind(firmId, REAL_EVENT_ID)
      .first();
    expect(row).toBeNull(); // reverted, not stuck claimed with nothing sent

    let sentOnRetry = false;
    await runRuleChangeAlertPass(env, {
      send: async (to) => {
        if (to === adminEmail) sentOnRetry = true;
        return true;
      },
    });
    expect(sentOnRetry).toBe(true);
  });

  it("a demo_locked firm is skipped entirely -- no send, no claim", async () => {
    const { firmId, adminEmail } = await newFirmWithRosterLicense("e2e-demo-locked", REAL_EVENT_STATE);
    await env.DB.prepare("UPDATE firms SET demo_locked = 1 WHERE id = ?1").bind(firmId).run();

    let sent = false;
    await runRuleChangeAlertPass(env, {
      send: async (to) => {
        if (to === adminEmail) sent = true;
        return true;
      },
    });
    expect(sent).toBe(false);

    const row = await env.DB.prepare("SELECT * FROM firm_rule_change_notifications WHERE firm_id = ?1 AND event_id = ?2")
      .bind(firmId, REAL_EVENT_ID)
      .first();
    expect(row).toBeNull();
  });

  it("the daily send cap halts the pass without erroring", async () => {
    await newFirmWithRosterLicense("e2e-cap-a", REAL_EVENT_STATE);
    await newFirmWithRosterLicense("e2e-cap-b", REAL_EVENT_STATE);
    const { checkAndCountRuleChangeAlertSend } = await import("../src/sender");
    await checkAndCountRuleChangeAlertSend(env.DB, 1); // consumes the only slot for today

    let sends = 0;
    const summary = await runRuleChangeAlertPass(
      { ...env, RULE_CHANGE_ALERT_DAILY_SEND_CAP: "1" },
      {
        send: async () => {
          sends += 1;
          return true;
        },
      }
    );
    expect(sends).toBe(0);
    expect(summary.errors.some((e) => e.error.includes("daily send cap"))).toBe(true);
  });

  it("AuditLab ALERT-2: a firm whose admin_email is permanently suppressed is skipped -- no send, no claim", async () => {
    const { firmId, adminEmail } = await newFirmWithRosterLicense("e2e-suppressed", REAL_EVENT_STATE);
    // Suppress via the subscriber mechanism this store fn reads -- same
    // stop_reason values isPermanentlySuppressed() already treats as
    // permanent (unsubscribed/hard_bounced/spam_complaint).
    await env.DB
      .prepare(
        `INSERT INTO subscribers (id, email, cooldown_key, state_slug, deadline_fields, status, stop_reason, stopped_at, confirm_token, unsubscribe_token, renewed_token, created_at)
         VALUES (?1, ?2, ?2, 'ohio', '{}', 'stopped', 'unsubscribed', datetime('now'), ?3, ?4, ?5, datetime('now'))`
      )
      .bind(store.newToken(), adminEmail, store.newToken(), store.newToken(), store.newToken())
      .run();

    let sent = false;
    const summary = await runRuleChangeAlertPass(env, {
      send: async (to) => {
        if (to === adminEmail) sent = true;
        return true;
      },
    });
    expect(sent).toBe(false);
    expect(summary.errors.some((e) => e.error.includes("permanently suppressed"))).toBe(true);

    const row = await env.DB.prepare("SELECT * FROM firm_rule_change_notifications WHERE firm_id = ?1 AND event_id = ?2")
      .bind(firmId, REAL_EVENT_ID)
      .first();
    expect(row).toBeNull(); // not claimed either
  });
});

describe("isEmailableRuleChangeEvent() -- AuditLab ALERT-1", () => {
  function baseEvent(overrides: Partial<import("../src/scheduler").RuleChangeEvent> = {}) {
    return {
      event_id: "test-event",
      jurisdiction_slug: "test",
      jurisdiction: "Test",
      effective_date: "2027-01-01",
      kind: "rule_change",
      upcoming: true,
      status: "ENACTED",
      needs_reverification: false,
      ...overrides,
    };
  }

  // AuditLab TEST-6 (2026-08-27): isEmailableRuleChangeEvent's new `today`
  // param defaults to real `new Date()` -- these assertions called it
  // without one, so they silently started resolving against real time
  // (previously inert, since `upcoming: true` was trusted unconditionally).
  // baseEvent()'s hardcoded "2027-01-01" would have flipped this whole
  // block to failing on that date, the exact stale-fixture class this same
  // commit fixed for REAL_EVENT_ID above. A fixed `today` makes these
  // permanently time-independent instead of postponing the fuse.
  const FIXED_TODAY = new Date("2026-08-27T00:00:00Z");

  it("emails a real ENACTED, fully-reverified, upcoming rule change", async () => {
    const { isEmailableRuleChangeEvent } = await import("../src/scheduler");
    expect(isEmailableRuleChangeEvent(baseEvent(), FIXED_TODAY)).toBe(true);
  });

  it("does NOT email a future event flagged needs_reverification -- the exact Louisiana shape, but upcoming", async () => {
    const { isEmailableRuleChangeEvent } = await import("../src/scheduler");
    expect(isEmailableRuleChangeEvent(baseEvent({ needs_reverification: true }), FIXED_TODAY)).toBe(false);
  });

  it("does NOT email a PROPOSED (not yet ENACTED) rule with a future effective date -- the exact Idaho shape, but upcoming", async () => {
    const { isEmailableRuleChangeEvent } = await import("../src/scheduler");
    expect(isEmailableRuleChangeEvent(baseEvent({ status: "PROPOSED" }), FIXED_TODAY)).toBe(false);
  });

  it("still excludes source_conflict/non-upcoming/missing-date, unchanged from before this fix", async () => {
    const { isEmailableRuleChangeEvent } = await import("../src/scheduler");
    expect(isEmailableRuleChangeEvent(baseEvent({ kind: "source_conflict" }), FIXED_TODAY)).toBe(false);
    expect(isEmailableRuleChangeEvent(baseEvent({ upcoming: false }), FIXED_TODAY)).toBe(false);
    expect(isEmailableRuleChangeEvent(baseEvent({ effective_date: "" }), FIXED_TODAY)).toBe(false);
  });

  // AuditLab REGEN-8 (2026-08-26): `upcoming` is a boolean baked into the
  // deployed JSON at whatever moment the data pipeline last ran (a manual
  // step). Simulated: an event whose effective_date has since passed but
  // whose stored `upcoming: true` never got a chance to flip, because
  // nobody re-ran the sync + redeployed the worker between the record's
  // last build and today. Before this fix, `isEmailableRuleChangeEvent`
  // trusted the stale flag and would keep sending "an upcoming rule
  // change" about a change already in force -- AuditLab's simulation
  // predicted exactly this for Missouri on 2026-08-28.
  it("does NOT email once effective_date has passed, even with a stale upcoming:true flag baked into the data", async () => {
    const { isEmailableRuleChangeEvent } = await import("../src/scheduler");
    const staleEvent = baseEvent({ effective_date: "2026-08-28", upcoming: true });
    expect(isEmailableRuleChangeEvent(staleEvent, new Date("2026-08-27T00:00:00Z"))).toBe(true);
    expect(isEmailableRuleChangeEvent(staleEvent, new Date("2026-08-28T00:00:00Z"))).toBe(false);
    expect(isEmailableRuleChangeEvent(staleEvent, new Date("2026-08-29T00:00:00Z"))).toBe(false);
  });

  it("a stored upcoming:false is never overridden to true by a future effective_date -- pure tightening, never widening", async () => {
    const { isEmailableRuleChangeEvent } = await import("../src/scheduler");
    expect(
      isEmailableRuleChangeEvent(baseEvent({ effective_date: "2027-01-01", upcoming: false }), new Date("2026-08-27T00:00:00Z"))
    ).toBe(false);
  });
});

describe("buildRuleChangeNotificationEmail() -- AuditLab ALERT-3 staff half", () => {
  // Filed 2026-08-26, re-verified live 2026-08-29: this builder hardcoded
  // "mobility"/"practice-privilege" wording from when the rule-change feed
  // was mobility-only. Today every emailable event is a DiffLab regwatch
  // event -- AuditLab's live production example was Oklahoma's licensure-
  // pathway change (topic "CPA regulatory/statutory change") going out
  // headed "Oklahoma mobility rule change".
  it("a non-mobility topic gets generic wording -- no 'mobility' or 'practice-privilege' claim, no Practice Privilege Check CTA", async () => {
    const { buildRuleChangeNotificationEmail } = await import("../src/emails");
    const built = buildRuleChangeNotificationEmail(
      "Acme CPA",
      "Oklahoma",
      "Oklahoma",
      "HB4317 adds a bachelor's-degree-plus-experience pathway to CPA licensure.",
      "November 1, 2026",
      "https://example.gov/hb4317",
      "https://deadline-radar.com/unsubscribe?token=x",
      "CPA regulatory/statutory change"
    );
    expect(built.subject).toBe("Oklahoma CPA regulatory/statutory change -- Acme CPA");
    expect(built.subject.toLowerCase()).not.toContain("mobility");
    expect(built.textBody.toLowerCase()).not.toContain("mobility");
    expect(built.textBody.toLowerCase()).not.toContain("practice-privilege");
    expect(built.textBody).not.toContain("Practice Privilege Check");
    expect(built.textBody).toContain("confirm directly with the Oklahoma board of accountancy");
    expect(built.htmlBody.toLowerCase()).not.toContain("mobility");
    expect(built.htmlBody).not.toContain("Practice Privilege Check");
  });

  it("a genuinely mobility/practice-privilege topic keeps the existing wording and CTA -- no regression", async () => {
    const { buildRuleChangeNotificationEmail } = await import("../src/emails");
    const built = buildRuleChangeNotificationEmail(
      "Acme CPA",
      "Illinois",
      "Illinois",
      "Illinois adopts substantial equivalency for practice privilege.",
      "January 1, 2027",
      null,
      "https://deadline-radar.com/unsubscribe?token=x",
      "practice privilege (mobility)"
    );
    expect(built.subject).toBe("Illinois mobility rule change -- Acme CPA");
    expect(built.textBody).toContain("practice-privilege rule change");
    expect(built.textBody).toContain("check Practice Privilege Check or confirm directly with the Illinois board of accountancy");
  });

  it("a missing/empty topic (older cached page) falls back to generic wording, not mobility -- fails safe, not to the old wrong default", async () => {
    const { buildRuleChangeNotificationEmail } = await import("../src/emails");
    const built = buildRuleChangeNotificationEmail(
      "Acme CPA",
      "Missouri",
      "Missouri",
      "A rule changed.",
      "August 28, 2026",
      null,
      "https://deadline-radar.com/unsubscribe?token=x"
    );
    expect(built.subject).toBe("Missouri practice/license rule change -- Acme CPA");
    expect(built.subject.toLowerCase()).not.toContain("mobility");
  });
});

describe("POST /firm/rule-change/notify forwards topic end to end", () => {
  const SENDGRID_URL = "https://api.sendgrid.com/v3/mail/send";

  it("a non-mobility topic in the request body reaches the actual sent email, not the old hardcoded mobility wording", async () => {
    const { firmId, memberId } = await newFirmWithRosterLicense("alert3-e2e", "oklahoma");
    const cookie = await sessionCookieFor(firmId, memberId);

    const captured: Array<{ subject: string }> = [];
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = typeof input === "string" ? input : (input as Request).url;
      if (url === SENDGRID_URL) {
        const body = JSON.parse(String(init?.body)) as { subject: string };
        captured.push({ subject: body.subject });
        return new Response(null, { status: 202 });
      }
      throw new Error(`unexpected fetch in ALERT-3 e2e test: ${url}`);
    });
    try {
      const resp = await workerFetch(
        new Request(`${BASE}/firm/rule-change/notify`, {
          method: "POST",
          headers: { "content-type": "application/json", Cookie: cookie, Origin: BASE, "cf-connecting-ip": "203.0.113.90" },
          body: JSON.stringify({
            state_slug: "oklahoma",
            jurisdiction: "Oklahoma",
            summary: "HB4317 adds a bachelor's-degree-plus-experience pathway to CPA licensure.",
            effective_date_label: "November 1, 2026",
            citation_url: "",
            topic: "CPA regulatory/statutory change",
          }),
        }),
        { SENDGRID_API_KEY: "test-key-not-real" }
      );
      expect(resp.status).toBe(200);
      const respBody = (await resp.json()) as { sent: number };
      expect(respBody.sent).toBe(1);
      expect(captured).toHaveLength(1);
      const sentSubject = captured[0]?.subject ?? "";
      expect(sentSubject).toBe("Oklahoma CPA regulatory/statutory change -- alert3-e2e LLP");
      expect(sentSubject.toLowerCase()).not.toContain("mobility");
    } finally {
      fetchSpy.mockRestore();
    }
  });
});

describe("PATCH /firm/rule-change-alerts", () => {
  it("a partner can disable and re-enable alerts", async () => {
    const { firmId, memberId } = await newFirmWithRosterLicense("patch-toggle", "texas");
    const cookie = await sessionCookieFor(firmId, memberId);

    const off = await SELF.fetch(`${BASE}/firm/rule-change-alerts`, {
      method: "PATCH",
      headers: { "content-type": "application/json", "cf-connecting-ip": "203.0.113.250", Cookie: cookie },
      body: JSON.stringify({ enabled: false }),
    });
    expect(off.status).toBe(200);
    const offJson = (await off.json()) as { rule_change_alerts_enabled: boolean };
    expect(offJson.rule_change_alerts_enabled).toBe(false);

    const row = await env.DB.prepare("SELECT rule_change_alerts_enabled FROM firms WHERE id = ?1").bind(firmId).first<{ rule_change_alerts_enabled: number }>();
    expect(row?.rule_change_alerts_enabled).toBe(0);

    const on = await SELF.fetch(`${BASE}/firm/rule-change-alerts`, {
      method: "PATCH",
      headers: { "content-type": "application/json", "cf-connecting-ip": "203.0.113.251", Cookie: cookie },
      body: JSON.stringify({ enabled: true }),
    });
    expect(on.status).toBe(200);
  });

  it("defaults to enabled for a brand-new firm", async () => {
    const { firmId } = await newFirmWithRosterLicense("patch-default", "texas");
    const row = await env.DB.prepare("SELECT rule_change_alerts_enabled FROM firms WHERE id = ?1").bind(firmId).first<{ rule_change_alerts_enabled: number }>();
    expect(row?.rule_change_alerts_enabled).toBe(1);
  });

  it("401s with no session", async () => {
    const resp = await SELF.fetch(`${BASE}/firm/rule-change-alerts`, {
      method: "PATCH",
      headers: { "content-type": "application/json", "cf-connecting-ip": "203.0.113.252" },
      body: JSON.stringify({ enabled: false }),
    });
    expect(resp.status).toBe(401);
  });

  it("400s on a missing/invalid enabled value", async () => {
    const { firmId, memberId } = await newFirmWithRosterLicense("patch-invalid", "texas");
    const cookie = await sessionCookieFor(firmId, memberId);
    const resp = await SELF.fetch(`${BASE}/firm/rule-change-alerts`, {
      method: "PATCH",
      headers: { "content-type": "application/json", "cf-connecting-ip": "203.0.113.253", Cookie: cookie },
      body: JSON.stringify({}),
    });
    expect(resp.status).toBe(400);
  });

  it("a Staff-role member is refused (partner/office_manager only)", async () => {
    const { firmId, memberId: partnerId } = await newFirmWithRosterLicense("patch-role-gate", "texas");
    const staffEmail = `staffer-${Date.now()}@examplefirm.com`;
    const { id: staffMemberId } = await store.createFirmMember(env.DB, { firmId, email: staffEmail, name: "Staffer", role: "staff", alreadyJoined: true });
    const cookie = await sessionCookieFor(firmId, staffMemberId);
    const resp = await SELF.fetch(`${BASE}/firm/rule-change-alerts`, {
      method: "PATCH",
      headers: { "content-type": "application/json", "cf-connecting-ip": "203.0.113.254", Cookie: cookie },
      body: JSON.stringify({ enabled: false }),
    });
    expect(resp.status).toBe(403);
    void partnerId;
  });

  it("a cross-site POST (mismatched Origin) is refused", async () => {
    const { firmId, memberId } = await newFirmWithRosterLicense("patch-csrf", "texas");
    const cookie = await sessionCookieFor(firmId, memberId);
    const resp = await workerFetch(
      new Request(`${BASE}/firm/rule-change-alerts`, {
        method: "PATCH",
        headers: {
          "content-type": "application/json",
          "cf-connecting-ip": "203.0.113.255",
          Cookie: cookie,
          Origin: "https://evil.example",
        },
        body: JSON.stringify({ enabled: false }),
      })
    );
    expect(resp.status).toBe(400);
  });
});
