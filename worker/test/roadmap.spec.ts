/**
 * Task #19 (2026-08-06, migration 0029): post-signup feature-request
 * questionnaire (private, per-firm) + what's left of the public /roadmap/
 * voting page after Devin retired it 2026-10-16 (near-zero engagement --
 * max vote_count 1). Voting (GET /roadmap-data, POST /roadmap/vote) and the
 * signup entry point (POST /roadmap/notify-signup) are gone; this file now
 * only tests the confirm/unsubscribe half that stays live for anyone who
 * already signed up, seeded directly via store.createFeatureIdeaNotifySignup
 * rather than through the retired HTTP route. See migration 0029's own
 * docstring for the full original design reasoning.
 */
import { env, SELF } from "cloudflare:test";
import { describe, expect, it } from "vitest";
import * as store from "../src/store";

const BASE = "https://deadline-radar.com";
const SEEDED_IDEA_ID = "idea-sms-reminders";

async function createFirmWithSession(name: string, adminEmail: string): Promise<{ firmId: string; cookie: string }> {
  const firm = await store.createFirm(env.DB, { name, adminEmail });
  const { rawSessionToken } = await store.createSession(env.DB, firm.id);
  return { firmId: firm.id, cookie: `dr_firm_session=${rawSessionToken}` };
}

describe("GET/POST /roadmap/notify-confirm", () => {
  it("confirms a real signup end-to-end through the actual HTTP action route", async () => {
    const email = `roadmapconfirm-${Date.now()}@example.com`;
    const signup = await store.createFeatureIdeaNotifySignup(env.DB, SEEDED_IDEA_ID, email);
    expect(signup).toBeTruthy();

    const page = await SELF.fetch(`${BASE}/roadmap/notify-confirm?token=${encodeURIComponent(signup!.rawToken)}`);
    expect(page.status).toBe(200);
    const html = await page.text();
    const nonce = /name="action_csrf" value="([^"]+)"/.exec(html)?.[1] ?? "";

    const confirmResp = await SELF.fetch(`${BASE}/roadmap/notify-confirm`, {
      method: "POST",
      headers: { "content-type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({ token: signup!.rawToken, action_csrf: nonce }).toString(),
    });
    expect(confirmResp.status).toBe(200);

    const row = await env.DB.prepare("SELECT confirmed_at FROM feature_idea_notify_signups WHERE idea_id = ?1 AND email = ?2")
      .bind(SEEDED_IDEA_ID, email.toLowerCase())
      .first<{ confirmed_at: string | null }>();
    expect(row?.confirmed_at).toBeTruthy();
  });

  it("404s for an unknown/already-used token", async () => {
    const page = await SELF.fetch(`${BASE}/roadmap/notify-confirm?token=not-a-real-token`);
    const html = await page.text();
    const nonce = /name="action_csrf" value="([^"]+)"/.exec(html)?.[1] ?? "";
    const resp = await SELF.fetch(`${BASE}/roadmap/notify-confirm`, {
      method: "POST",
      headers: { "content-type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({ token: "not-a-real-token", action_csrf: nonce }).toString(),
    });
    expect(resp.status).toBe(404);
  });
});

describe("AuditLab UNSUB-4: /unsubscribe/feature-idea + store.optOutFeatureIdeaSignupByToken", () => {
  async function confirmedSignup(email: string): Promise<string> {
    const signup = await store.createFeatureIdeaNotifySignup(env.DB, SEEDED_IDEA_ID, email);
    await store.confirmFeatureIdeaNotifySignup(env.DB, signup!.rawToken);
    const row = await env.DB.prepare("SELECT id FROM feature_idea_notify_signups WHERE idea_id = ?1 AND email = ?2")
      .bind(SEEDED_IDEA_ID, email.toLowerCase())
      .first<{ id: string }>();
    return row!.id;
  }

  it("store: opting out removes the row from listConfirmedUnnotifiedSignupsForIdea", async () => {
    const email = `unsub4-${Date.now()}@example.com`;
    const id = await confirmedSignup(email);

    const before = await store.listConfirmedUnnotifiedSignupsForIdea(env.DB, SEEDED_IDEA_ID);
    expect(before.some((r) => r.id === id)).toBe(true);

    const optedOut = await store.optOutFeatureIdeaSignupByToken(env.DB, id);
    expect(optedOut).toBe(true);

    const after = await store.listConfirmedUnnotifiedSignupsForIdea(env.DB, SEEDED_IDEA_ID);
    expect(after.some((r) => r.id === id)).toBe(false);
  });

  it("store: idempotent -- a second opt-out on the same token reports no change, not an error", async () => {
    const email = `unsub4-idempotent-${Date.now()}@example.com`;
    const id = await confirmedSignup(email);

    expect(await store.optOutFeatureIdeaSignupByToken(env.DB, id)).toBe(true);
    expect(await store.optOutFeatureIdeaSignupByToken(env.DB, id)).toBe(false);
  });

  it("store: unaffected signup rows for OTHER ideas/emails still list normally", async () => {
    const emailA = `unsub4-scope-a-${Date.now()}@example.com`;
    const emailB = `unsub4-scope-b-${Date.now()}@example.com`;
    const idA = await confirmedSignup(emailA);
    const idB = await confirmedSignup(emailB);

    await store.optOutFeatureIdeaSignupByToken(env.DB, idA);

    const remaining = await store.listConfirmedUnnotifiedSignupsForIdea(env.DB, SEEDED_IDEA_ID);
    expect(remaining.some((r) => r.id === idA)).toBe(false);
    expect(remaining.some((r) => r.id === idB)).toBe(true);
  });

  it("HTTP: GET renders a confirm page, POST performs the opt-out end-to-end", async () => {
    const email = `unsub4-http-${Date.now()}@example.com`;
    const id = await confirmedSignup(email);

    const page = await SELF.fetch(`${BASE}/unsubscribe/feature-idea?token=${encodeURIComponent(id)}`);
    expect(page.status).toBe(200);
    const html = await page.text();
    expect(html).toContain("Unsubscribe");
    const nonce = /name="action_csrf" value="([^"]+)"/.exec(html)?.[1] ?? "";

    const postResp = await SELF.fetch(`${BASE}/unsubscribe/feature-idea`, {
      method: "POST",
      headers: { "content-type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({ token: id, action_csrf: nonce }).toString(),
    });
    expect(postResp.status).toBe(200);

    const remaining = await store.listConfirmedUnnotifiedSignupsForIdea(env.DB, SEEDED_IDEA_ID);
    expect(remaining.some((r) => r.id === id)).toBe(false);
  });

  it("HTTP: 404s for an unknown token", async () => {
    const page = await SELF.fetch(`${BASE}/unsubscribe/feature-idea?token=not-a-real-token`);
    const html = await page.text();
    const nonce = /name="action_csrf" value="([^"]+)"/.exec(html)?.[1] ?? "";
    const resp = await SELF.fetch(`${BASE}/unsubscribe/feature-idea`, {
      method: "POST",
      headers: { "content-type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({ token: "not-a-real-token", action_csrf: nonce }).toString(),
    });
    expect(resp.status).toBe(404);
  });
});

describe("POST /firm/questionnaire and /firm/questionnaire/dismiss", () => {
  it("GET /firm/licenses reports questionnaire_pending:true for a brand-new firm", async () => {
    const { cookie } = await createFirmWithSession("Questionnaire Firm", `questionnaire-${Date.now()}@example.com`);
    const resp = await SELF.fetch(`${BASE}/firm/licenses`, { headers: { Cookie: cookie } });
    const body = (await resp.json()) as { questionnaire_pending: boolean };
    expect(body.questionnaire_pending).toBe(true);
  });

  it("submitting the questionnaire stores the response and flips questionnaire_pending to false", async () => {
    const { cookie, firmId } = await createFirmWithSession("Questionnaire Submit Firm", `questionnairesub-${Date.now()}@example.com`);
    const submitResp = await SELF.fetch(`${BASE}/firm/questionnaire`, {
      method: "POST",
      headers: { "content-type": "application/json", Cookie: cookie },
      body: JSON.stringify({ selected_features: ["SMS reminders", "API access"], other_text: "Also: dark mode" }),
    });
    expect(submitResp.status).toBe(200);

    const row = await env.DB.prepare("SELECT selected_features, other_text FROM feature_questionnaire_responses WHERE firm_id = ?1")
      .bind(firmId)
      .first<{ selected_features: string; other_text: string | null }>();
    expect(JSON.parse(row!.selected_features)).toEqual(["SMS reminders", "API access"]);
    expect(row!.other_text).toBe("Also: dark mode");

    const licensesResp = await SELF.fetch(`${BASE}/firm/licenses`, { headers: { Cookie: cookie } });
    const licensesBody = (await licensesResp.json()) as { questionnaire_pending: boolean };
    expect(licensesBody.questionnaire_pending).toBe(false);
  });

  it("skipping (dismiss) flips questionnaire_pending to false without a response row", async () => {
    const { cookie, firmId } = await createFirmWithSession("Questionnaire Skip Firm", `questionnaireskip-${Date.now()}@example.com`);
    const dismissResp = await SELF.fetch(`${BASE}/firm/questionnaire/dismiss`, { method: "POST", headers: { Cookie: cookie } });
    expect(dismissResp.status).toBe(200);

    const row = await env.DB.prepare("SELECT 1 FROM feature_questionnaire_responses WHERE firm_id = ?1").bind(firmId).first();
    expect(row).toBeNull();

    const licensesResp = await SELF.fetch(`${BASE}/firm/licenses`, { headers: { Cookie: cookie } });
    const licensesBody = (await licensesResp.json()) as { questionnaire_pending: boolean };
    expect(licensesBody.questionnaire_pending).toBe(false);
  });

  it("401s with no session for both routes", async () => {
    expect((await SELF.fetch(`${BASE}/firm/questionnaire`, { method: "POST" })).status).toBe(401);
    expect((await SELF.fetch(`${BASE}/firm/questionnaire/dismiss`, { method: "POST" })).status).toBe(401);
  });
});

// Roadmap #28 (2026-08-06, roadmap_items table): guided onboarding
// checklist. Only the server-side dismiss half is testable here -- the
// four step checkmarks themselves are computed client-side (roster/CPE
// data already in memory, or a localStorage visit flag), not a separate
// endpoint.
describe("POST /firm/onboarding-checklist/dismiss", () => {
  it("401s with no session", async () => {
    expect((await SELF.fetch(`${BASE}/firm/onboarding-checklist/dismiss`, { method: "POST" })).status).toBe(401);
  });

  it("GET /firm/licenses reports onboarding_checklist_pending:true for a brand-new firm, false after dismiss", async () => {
    const { cookie } = await createFirmWithSession("Onboarding Checklist Firm", `onboardingchecklist-${Date.now()}@example.com`);
    const before = await SELF.fetch(`${BASE}/firm/licenses`, { headers: { Cookie: cookie } });
    const beforeBody = (await before.json()) as { onboarding_checklist_pending: boolean };
    expect(beforeBody.onboarding_checklist_pending).toBe(true);

    const dismissResp = await SELF.fetch(`${BASE}/firm/onboarding-checklist/dismiss`, { method: "POST", headers: { Cookie: cookie } });
    expect(dismissResp.status).toBe(200);

    const after = await SELF.fetch(`${BASE}/firm/licenses`, { headers: { Cookie: cookie } });
    const afterBody = (await after.json()) as { onboarding_checklist_pending: boolean };
    expect(afterBody.onboarding_checklist_pending).toBe(false);
  });

  it("is independent of the feature-request questionnaire's own pending flag", async () => {
    const { cookie } = await createFirmWithSession("Independent Pending Firm", `independentpending-${Date.now()}@example.com`);
    await SELF.fetch(`${BASE}/firm/onboarding-checklist/dismiss`, { method: "POST", headers: { Cookie: cookie } });
    const resp = await SELF.fetch(`${BASE}/firm/licenses`, { headers: { Cookie: cookie } });
    const body = (await resp.json()) as { onboarding_checklist_pending: boolean; questionnaire_pending: boolean };
    expect(body.onboarding_checklist_pending).toBe(false);
    expect(body.questionnaire_pending).toBe(true);
  });
});

// Roadmap #30 (2026-08-07, roadmap_items table): in-app product tour. Only
// the server-side dismiss half is testable here -- the 4-step sequence and
// its tooltip positioning are client-side only, same reasoning as #28's own
// test comment just above.
describe("POST /firm/product-tour/dismiss", () => {
  it("401s with no session", async () => {
    expect((await SELF.fetch(`${BASE}/firm/product-tour/dismiss`, { method: "POST" })).status).toBe(401);
  });

  it("GET /firm/licenses reports product_tour_pending:true for a brand-new firm, false after dismiss", async () => {
    const { cookie } = await createFirmWithSession("Product Tour Firm", `producttour-${Date.now()}@example.com`);
    const before = await SELF.fetch(`${BASE}/firm/licenses`, { headers: { Cookie: cookie } });
    const beforeBody = (await before.json()) as { product_tour_pending: boolean };
    expect(beforeBody.product_tour_pending).toBe(true);

    const dismissResp = await SELF.fetch(`${BASE}/firm/product-tour/dismiss`, { method: "POST", headers: { Cookie: cookie } });
    expect(dismissResp.status).toBe(200);

    const after = await SELF.fetch(`${BASE}/firm/licenses`, { headers: { Cookie: cookie } });
    const afterBody = (await after.json()) as { product_tour_pending: boolean };
    expect(afterBody.product_tour_pending).toBe(false);
  });

  it("is independent of the onboarding checklist's own pending flag", async () => {
    const { cookie } = await createFirmWithSession("Independent Tour Pending Firm", `independenttourpending-${Date.now()}@example.com`);
    await SELF.fetch(`${BASE}/firm/product-tour/dismiss`, { method: "POST", headers: { Cookie: cookie } });
    const resp = await SELF.fetch(`${BASE}/firm/licenses`, { headers: { Cookie: cookie } });
    const body = (await resp.json()) as { product_tour_pending: boolean; onboarding_checklist_pending: boolean };
    expect(body.product_tour_pending).toBe(false);
    expect(body.onboarding_checklist_pending).toBe(true);
  });

  it("is idempotent -- a second dismiss doesn't move the timestamp", async () => {
    const { cookie, firmId } = await createFirmWithSession("Idempotent Tour Dismiss Firm", `idempotenttour-${Date.now()}@example.com`);
    await SELF.fetch(`${BASE}/firm/product-tour/dismiss`, { method: "POST", headers: { Cookie: cookie } });
    const firmAfterFirst = await store.getFirmById(env.DB, firmId);
    const firstTimestamp = firmAfterFirst?.product_tour_dismissed_at;
    expect(firstTimestamp).toBeTruthy();

    await SELF.fetch(`${BASE}/firm/product-tour/dismiss`, { method: "POST", headers: { Cookie: cookie } });
    const firmAfterSecond = await store.getFirmById(env.DB, firmId);
    expect(firmAfterSecond?.product_tour_dismissed_at).toBe(firstTimestamp);
  });
});

describe("Orchestrator finding (2026-08-27): the shared demo firm never re-shows the tour/checklist once ANYONE dismisses them", () => {
  it("stays pending:true for BOTH panels on the demo firm even after a real dismiss -- unlike a real firm, which stays false", async () => {
    const { cookie, firmId } = await createFirmWithSession("Demo Tour Reset Firm", `demotourreset-${Date.now()}@example.com`);
    await env.DB.prepare("UPDATE firms SET demo_locked = 1 WHERE id = ?1").bind(firmId).run();

    // A visitor dismisses both, same as any real firm could.
    const dismissTour = await SELF.fetch(`${BASE}/firm/product-tour/dismiss`, { method: "POST", headers: { Cookie: cookie } });
    expect(dismissTour.status).toBe(200);
    const dismissChecklist = await SELF.fetch(`${BASE}/firm/onboarding-checklist/dismiss`, { method: "POST", headers: { Cookie: cookie } });
    expect(dismissChecklist.status).toBe(200);

    // DEMO-3 (pre-existing, both handlers) already no-ops the WRITE for a
    // demo_locked firm -- dismiss returns 200 but never actually calls
    // store.dismissProductTour()/dismissOnboardingChecklist(), so the
    // columns stay null. This fix is defense-in-depth for however the LIVE
    // demo firm's row ended up with these set anyway (predates DEMO-3, or
    // some other path) -- it doesn't depend on DEMO-3's write-side no-op,
    // it makes the READ side correct regardless of the columns' actual value.
    const firmRow = await store.getFirmById(env.DB, firmId);
    expect(firmRow?.product_tour_dismissed_at).toBeNull();
    expect(firmRow?.onboarding_checklist_dismissed_at).toBeNull();

    // The NEXT session (a later visitor, or the same one reloading) still
    // sees both as pending -- the whole point of the fix, and true
    // regardless of whether the columns above are null or (as on the live
    // demo firm right now) already set from before DEMO-3 existed.
    const after = await SELF.fetch(`${BASE}/firm/licenses`, { headers: { Cookie: cookie } });
    const afterBody = (await after.json()) as { product_tour_pending: boolean; onboarding_checklist_pending: boolean };
    expect(afterBody.product_tour_pending).toBe(true);
    expect(afterBody.onboarding_checklist_pending).toBe(true);
  });

  it("positive control: reports pending:true even with both columns FORCED non-null -- the exact state the live demo firm is actually in right now", async () => {
    const { cookie, firmId } = await createFirmWithSession("Demo Tour Forced Dismissed Firm", `demotourforced-${Date.now()}@example.com`);
    await env.DB.prepare("UPDATE firms SET demo_locked = 1 WHERE id = ?1").bind(firmId).run();
    // Bypasses both handlers' own DEMO-3 no-op -- simulates however the
    // live row actually got into this state (predates DEMO-3, or a direct
    // DB path), which is the state this fix specifically has to override.
    await env.DB.prepare(
      "UPDATE firms SET product_tour_dismissed_at = ?1, onboarding_checklist_dismissed_at = ?1 WHERE id = ?2"
    ).bind(new Date().toISOString(), firmId).run();

    const resp = await SELF.fetch(`${BASE}/firm/licenses`, { headers: { Cookie: cookie } });
    const body = (await resp.json()) as { product_tour_pending: boolean; onboarding_checklist_pending: boolean };
    expect(body.product_tour_pending).toBe(true);
    expect(body.onboarding_checklist_pending).toBe(true);
  });

  it("a real (non-demo) firm is unaffected -- dismiss still sticks", async () => {
    const { cookie } = await createFirmWithSession("Real Firm Tour Sticks", `realfirmtoursticks-${Date.now()}@example.com`);
    await SELF.fetch(`${BASE}/firm/product-tour/dismiss`, { method: "POST", headers: { Cookie: cookie } });
    await SELF.fetch(`${BASE}/firm/onboarding-checklist/dismiss`, { method: "POST", headers: { Cookie: cookie } });
    const after = await SELF.fetch(`${BASE}/firm/licenses`, { headers: { Cookie: cookie } });
    const afterBody = (await after.json()) as { product_tour_pending: boolean; onboarding_checklist_pending: boolean };
    expect(afterBody.product_tour_pending).toBe(false);
    expect(afterBody.onboarding_checklist_pending).toBe(false);
  });
});

describe("store.setFeatureIdeaStatus", () => {
  it("updates status and rejects an invalid value", async () => {
    const ok = await store.setFeatureIdeaStatus(env.DB, "idea-white-label", "in_progress");
    expect(ok).toBe(true);
    const row = await env.DB.prepare("SELECT status FROM feature_ideas WHERE id = ?1").bind("idea-white-label").first<{ status: string }>();
    expect(row?.status).toBe("in_progress");

    // Revert so this test is order-independent of anything else reading this idea's status.
    await store.setFeatureIdeaStatus(env.DB, "idea-white-label", "open");

    const bad = await store.setFeatureIdeaStatus(env.DB, "idea-white-label", "not-a-real-status" as never);
    expect(bad).toBe(false);
  });
});
