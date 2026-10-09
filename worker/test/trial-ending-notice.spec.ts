/**
 * TRIAL-3 (AuditLab 2026-10-09, Devin OK on option (a)): every new firm gets a
 * 14-day trial of all paid features and, before this pass, only OVER-cap firms
 * were ever warned it ends. runTrialEndingNoticePass() warns the rest.
 *
 * It is built DARK: gated by requireSendApproval(env, "trialEndingNotice"), a
 * name no approved list contains, until Devin consents to the exact text. These
 * tests prove it sends nothing unapproved, sends exactly once to exactly the
 * right firms when approved, never doubles up with runTrialEndingAlertPass(),
 * and is really dispatched by scheduled() (a pass that is only unit-tested and
 * never wired is the failure mode this file refuses to repeat).
 */
import { env } from "cloudflare:test";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as store from "../src/store";
import { buildTrialEndingNoticeEmail } from "../src/emails";
import { runTrialEndingAlertPass, runTrialEndingNoticePass } from "../src/scheduler";

const APPROVED = { SEND_APPROVED_PASSES: "trialEndingNotice", RESEND_API_KEY: "test-key" };

function ctx(): ExecutionContext {
  return { waitUntil() {}, passThroughOnException() {}, props: {} } as unknown as ExecutionContext;
}

async function makeFirm(name: string, staff: number, endsInDays: number, tier = "free"): Promise<{ firmId: string; email: string }> {
  const email = `tn-${crypto.randomUUID().slice(0, 8)}@example.com`;
  const firm = await store.createFirm(env.DB, { name, adminEmail: email });
  for (let i = 0; i < staff; i++) {
    await store.addPending(env.DB, {
      email: `s${i}-${crypto.randomUUID().slice(0, 8)}@example.com`,
      stateSlug: "georgia",
      deadlineFields: { license_type_id: "ga-individual" },
      firstName: null,
      deadlineSource: store.DEADLINE_SOURCE_COMPUTED,
      userDeadline: null,
      firmId: firm.id,
      staffLabel: null,
      skipConfirmation: true,
    });
  }
  await env.DB.prepare("UPDATE firms SET trial_ends_at = ?1, plan_tier = ?2 WHERE id = ?3")
    .bind(new Date(Date.now() + endsInDays * 86_400_000).toISOString(), tier, firm.id)
    .run();
  return { firmId: firm.id, email };
}

function collector(): { sent: { to: string; subject: string; text: string; html: string }[]; send: (to: string, built: { subject: string; textBody: string; htmlBody: string }) => Promise<boolean> } {
  const sent: { to: string; subject: string; text: string; html: string }[] = [];
  return { sent, send: async (to, built) => (sent.push({ to, subject: built.subject, text: built.textBody, html: built.htmlBody }), true) };
}

afterEach(() => vi.restoreAllMocks());

describe("runTrialEndingNoticePass -- built dark", () => {
  it("CONTROL: with the pass approved, an under-cap firm inside the window IS emailed (so the negatives below are not blind)", async () => {
    const f = await makeFirm("Notice Control Firm", 2, 2);
    const c = collector();
    await runTrialEndingNoticePass({ ...env, ...APPROVED } as never, { send: c.send });
    expect(c.sent.filter((s) => s.to === f.email)).toHaveLength(1);
  });

  it("sends NOTHING when 'trialEndingNotice' is not approved -- unset, empty, or only the OTHER trial pass approved", async () => {
    for (const approved of [undefined, "", "trialEndingAlert", "billingSyncAlert,trialEndingAlert"]) {
      const f = await makeFirm("Notice Dark Firm", 2, 2);
      const c = collector();
      const summary = await runTrialEndingNoticePass({ ...env, SEND_APPROVED_PASSES: approved, RESEND_API_KEY: "test-key" } as never, { send: c.send });
      expect(c.sent.filter((s) => s.to === f.email), `approved=${String(approved)}`).toHaveLength(0);
      expect(summary.checked).toBe(0);
      await env.DB.prepare("UPDATE firms SET trial_ending_soon_notified_at = ?1 WHERE id = ?2").bind("x", f.firmId).run(); // keep later iterations independent
    }
  });

  it("emails an under-cap firm exactly once, with the right days-remaining and links; a second run sends nothing", async () => {
    const f = await makeFirm("Notice Once Firm", 1, 2);
    const c = collector();
    await runTrialEndingNoticePass({ ...env, ...APPROVED } as never, { send: c.send });
    await runTrialEndingNoticePass({ ...env, ...APPROVED } as never, { send: c.send });
    const mine = c.sent.filter((s) => s.to === f.email);
    expect(mine).toHaveLength(1);
    expect(mine[0]!.subject).toBe("Your Deadline-Radar 14-day trial ends in 2 days");
    expect(mine[0]!.text).toContain("/pricing/");
    expect(mine[0]!.text).toContain("/firm-dashboard/#account");
    expect(mine[0]!.text).not.toContain("undefined");
  });

  it("a firm with a zero-staff roster (at cap trivially) is also warned; a firm AT the cap (3) is warned; a firm over the cap (4+) is not", async () => {
    const zero = await makeFirm("Notice Zero Firm", 0, 2);
    const three = await makeFirm("Notice Three Firm", 3, 2);
    const four = await makeFirm("Notice Four Firm", 4, 2);
    const c = collector();
    await runTrialEndingNoticePass({ ...env, ...APPROVED } as never, { send: c.send });
    expect(c.sent.filter((s) => s.to === zero.email)).toHaveLength(1);
    expect(c.sent.filter((s) => s.to === three.email)).toHaveLength(1);
    expect(c.sent.filter((s) => s.to === four.email)).toHaveLength(0); // runTrialEndingAlertPass owns this one
  });

  it("is disjoint from runTrialEndingAlertPass: running BOTH over the same firms sends each firm exactly one email in total", async () => {
    const under = await makeFirm("Notice Disjoint Under", 2, 2);
    const over = await makeFirm("Notice Disjoint Over", 6, 2);
    const c = collector();
    const both = { ...env, SEND_APPROVED_PASSES: "trialEndingNotice,trialEndingAlert", RESEND_API_KEY: "test-key" } as never;
    await runTrialEndingNoticePass(both, { send: c.send });
    await runTrialEndingAlertPass(both, { send: c.send });
    await runTrialEndingNoticePass(both, { send: c.send });
    await runTrialEndingAlertPass(both, { send: c.send });
    expect(c.sent.filter((s) => s.to === under.email).map((s) => s.subject)).toEqual(["Your Deadline-Radar 14-day trial ends in 2 days"]);
    expect(c.sent.filter((s) => s.to === over.email)).toHaveLength(1);
    expect(c.sent.filter((s) => s.to === over.email)[0]!.subject).toContain("pick which staff stay active");
  });

  it("skips: already-paid firms, trials outside the 3-day window (too early / already lapsed), demo and test tenants", async () => {
    const paid = await makeFirm("Notice Paid Firm", 2, 2, "firm_growth");
    const early = await makeFirm("Notice Early Firm", 2, 10);
    const lapsed = await makeFirm("Notice Lapsed Firm", 2, -1);
    const test = await makeFirm("Notice Test Tenant", 2, 2);
    await env.DB.prepare("UPDATE firms SET is_test_tenant = 1 WHERE id = ?1").bind(test.firmId).run();
    const demo = await makeFirm("Notice Demo Firm", 2, 2);
    await env.DB.prepare("UPDATE firms SET demo_locked = 1 WHERE id = ?1").bind(demo.firmId).run();
    const c = collector();
    await runTrialEndingNoticePass({ ...env, ...APPROVED } as never, { send: c.send });
    for (const f of [paid, early, lapsed, test, demo]) expect(c.sent.filter((s) => s.to === f.email)).toHaveLength(0);
  });

  it("a send that returns false is recorded as an error, not silently dropped", async () => {
    const f = await makeFirm("Notice Fail Firm", 1, 2);
    const summary = await runTrialEndingNoticePass({ ...env, ...APPROVED } as never, { send: async (to) => to !== f.email });
    expect(summary.errors.some((e) => e.firm_id === f.firmId && e.error.includes("send returned false"))).toBe(true);
  });

  it("WIRED: scheduled() really dispatches it -- approved => a Resend call to the firm; unapproved => none", async () => {
    const worker = (await import("../src/index")).default;
    const f = await makeFirm("Notice Wired Firm", 1, 2);
    const resendTo: string[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
      if (url.includes("resend.com")) {
        const body = JSON.parse(String(init?.body));
        resendTo.push(...(Array.isArray(body.to) ? body.to : [body.to]));
        return new Response(JSON.stringify({ id: "re_1" }), { status: 200 });
      }
      return new Response("{}", { status: 200 });
    });
    const waits: Promise<unknown>[] = [];
    const waitingCtx = { waitUntil: (p: Promise<unknown>) => void waits.push(p), passThroughOnException() {}, props: {} } as unknown as ExecutionContext;

    await worker.scheduled({} as ScheduledController, { ...env, SEND_APPROVED_PASSES: "billingSyncAlert", RESEND_API_KEY: "test-key" } as never, waitingCtx);
    await Promise.all(waits);
    expect(resendTo).not.toContain(f.email); // unapproved: the dispatch exists but sends nothing

    waits.length = 0;
    await worker.scheduled({} as ScheduledController, { ...env, ...APPROVED } as never, waitingCtx);
    await Promise.all(waits);
    expect(resendTo).toContain(f.email);
  });
});

describe("buildTrialEndingNoticeEmail -- the draft text", () => {
  const built = buildTrialEndingNoticeEmail({ firmName: "Acme CPA", daysRemaining: 3, pricingUrl: "https://deadline-radar.com/pricing/", dashboardUrl: "https://deadline-radar.com/firm-dashboard/#account" });
  it("states only facts the pricing table backs, and nothing it cannot back", () => {
    expect(built.subject).toBe("Your Deadline-Radar 14-day trial ends in 3 days");
    for (const must of ["14-day trial", "up to 3 staff", "no time limit", "Document storage", "Slack and Teams alerts", "daily firm-wide digest", "coverage overview", "$199 a year or $20 a month"]) {
      expect(built.textBody, must).toContain(must);
    }
    // nothing about deletion/retention/pausing/discounts, none of which this notice is entitled to promise
    for (const mustNot of ["delete", "paused", "discount", "refund", "cancel", "guarantee"]) {
      expect(built.textBody.toLowerCase(), mustNot).not.toContain(mustNot);
    }
  });
  it("AuditLab TRIAL-3 reject: no trial email claims 'every' paid feature (the referral row is NOT unlocked by the trial)", async () => {
    const { buildTrialEndingSoonEmail } = await import("../src/emails");
    const soon = buildTrialEndingSoonEmail({ firmName: "A", daysRemaining: 2, rosterCount: 6, freeCap: 3, dashboardUrl: "d" });
    for (const b of [built, soon]) {
      for (const body of [b.textBody, b.htmlBody]) {
        expect(body.toLowerCase()).not.toMatch(/every (deadline-radar )?paid feature/);
        expect(body).toContain("14-day trial of the ");
      }
    }
  });
  it("singular day, and firm names are neutralised (header injection in text, markup in HTML)", () => {
    expect(buildTrialEndingNoticeEmail({ firmName: "A", daysRemaining: 1, pricingUrl: "p", dashboardUrl: "d" }).subject).toBe("Your Deadline-Radar 14-day trial ends in 1 day");
    const evil = buildTrialEndingNoticeEmail({ firmName: "Evil\r\nBcc: x@y.z <script>alert(1)</script>", daysRemaining: 2, pricingUrl: "p", dashboardUrl: "d" });
    expect(evil.textBody).not.toMatch(/\r|\nBcc:/);
    expect(evil.htmlBody).not.toContain("<script>");
  });
});
