/**
 * SEC-71 + SEC-72 (LOW, 2026-11-06): the rule from sec70-free-refusal-order.spec.ts,
 * applied to every victim-keyed limiter rather than a list of named routes. Each
 * case sends the refusals a caller can trigger for free and asserts no row was
 * written, then sends a real request and asserts one was, so the zeros mean
 * something. The census at the bottom catches a new route that parses its body
 * under a limiter.
 */
import { env } from "cloudflare:test";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as store from "../src/store";
import { hashPassword } from "../src/password";
import indexSource from "../src/index.ts?raw";

const BASE = "https://deadline-radar.com";
const PASSWORD = "correct horse battery staple 72";
const BOGUS = "00000000-0000-4000-8000-000000000000";
const OVERSIZED = JSON.stringify({ pad: "x".repeat(9000) });

let extraEnv: Record<string, unknown> = {};
afterEach(() => {
  extraEnv = {};
  vi.restoreAllMocks();
});

async function workerFetch(request: Request): Promise<Response> {
  const worker = (await import("../src/index")).default;
  const ctx = { waitUntil() {}, passThroughOnException() {}, props: {} } as unknown as ExecutionContext;
  return worker.fetch(request, { ...env, ...extraEnv } as never, ctx);
}

let seq = 0;
function uniqueEmail(label: string): string {
  seq += 1;
  return `sec72-${label}-${Date.now()}-${seq}@examplefirm.com`;
}
function uniqueIp(): string {
  seq += 1;
  return `198.51.${Math.floor(seq / 250) + 100}.${(seq % 250) + 1}`;
}

interface Firm {
  firmId: string;
  memberId: string;
  cookie: string;
}

async function firm(): Promise<Firm> {
  const f = await store.createFirm(env.DB, { name: "SEC-72 LLP", adminEmail: uniqueEmail("owner") });
  await env.DB.prepare("UPDATE firms SET plan_tier = 'firm_scale' WHERE id = ?1").bind(f.id).run();
  await store.setFirmMemberPassword(env.DB, f.memberId, await hashPassword(PASSWORD));
  const { rawSessionToken } = await store.createSession(env.DB, f.id, f.memberId);
  return { firmId: f.id, memberId: f.memberId, cookie: `dr_firm_session=${rawSessionToken}` };
}

function send(method: string, path: string, cookie: string, body: BodyInit | null, contentType: string | null = "application/json"): Promise<Response> {
  const headers: Record<string, string> = { "cf-connecting-ip": uniqueIp(), Cookie: cookie, Origin: BASE };
  if (contentType) headers["content-type"] = contentType;
  return workerFetch(new Request(`${BASE}${path}`, { method, headers, body }));
}

async function slots(key: string, bucket: string): Promise<number> {
  const row = await env.DB.prepare("SELECT COUNT(*) AS n FROM rate_limit_hits WHERE ip = ?1 AND bucket = ?2").bind(key, bucket).first<{ n: number }>();
  return row?.n ?? 0;
}

async function license(f: Firm, email = uniqueEmail("licensee"), state = "georgia", type = "ga-individual"): Promise<string> {
  const created = await send("POST", "/firm/licenses", f.cookie, JSON.stringify({ staff_label: "Staff", email, state_slug: state, license_type_id: type }));
  expect(created.status).toBe(201);
  return (await created.json<{ id: string }>()).id;
}

type Arm = [method: string, path: string, body: string | null, status: number, contentType?: string | null];

/** Every free arm returns its refusal and writes nothing; the real request writes exactly one row. */
async function assertSite(cookie: string, key: string, bucket: string, free: Arm[], real: Arm): Promise<void> {
  const before = await slots(key, bucket);
  for (const [method, path, body, status, contentType] of free) {
    const resp = await send(method, path, cookie, body, contentType === undefined ? "application/json" : contentType);
    expect(resp.status, `${method} ${path} ${String(body).slice(0, 60)}`).toBe(status);
  }
  expect(await slots(key, bucket), `${bucket}: a free refusal spent a slot`).toBe(before);
  const [method, path, body, status, contentType] = real;
  const resp = await send(method, path, cookie, body, contentType === undefined ? "application/json" : contentType);
  expect(resp.status, `${method} ${path} (real)`).toBe(status);
  expect(await slots(key, bucket), `${bucket}: the real request should meet the limiter`).toBe(before + 1);
}

const J = (o: unknown) => JSON.stringify(o);

describe("SEC-72: the 8 subscriber sites", () => {
  async function subscriber(): Promise<{ email: string; cookie: string }> {
    const email = store.normalizeEmail(uniqueEmail("sub"));
    const { rawSessionToken } = await store.createSubscriberSession(env.DB, email);
    return { email, cookie: `dr_sub_session=${rawSessionToken}` };
  }

  it("change email: SEC-67's own site is free for every shape refusal", async () => {
    const s = await subscriber();
    await assertSite(s.cookie, s.email, "subscriber_change_email", [
      ["POST", "/subscriber/change-email", "{}", 400, null],
      ["POST", "/subscriber/change-email", "", 400],
      ["POST", "/subscriber/change-email", OVERSIZED, 400],
      ["POST", "/subscriber/change-email", "{", 400],
      ["POST", "/subscriber/change-email", J({ new_email: "nope" }), 400],
      ["POST", "/subscriber/change-email", J({ new_email: s.email }), 400],
    ], ["POST", "/subscriber/change-email", J({ new_email: uniqueEmail("new") }), 200]);
  });

  it("five malformed posts no longer lock out a real change-email", async () => {
    const s = await subscriber();
    for (let i = 0; i < 6; i++) await send("POST", "/subscriber/change-email", s.cookie, "{");
    expect((await send("POST", "/subscriber/change-email", s.cookie, J({ new_email: uniqueEmail("new") }))).status).toBe(200);
  });

  it("profile", async () => {
    const s = await subscriber();
    await assertSite(s.cookie, s.email, "subscriber_profile_update", [
      ["POST", "/subscriber/profile", OVERSIZED, 400],
      ["POST", "/subscriber/profile", "{", 400],
      ["POST", "/subscriber/profile", J({ first_name: "a\u0007" }), 400],
    ], ["POST", "/subscriber/profile", J({ first_name: "Ann" }), 200]);
  });

  it("reminder cadence, and a null clear still writes", async () => {
    const s = await subscriber();
    await assertSite(s.cookie, s.email, "subscriber_reminder_cadence", [
      ["PATCH", "/subscriber/reminder-cadence", OVERSIZED, 400],
      ["PATCH", "/subscriber/reminder-cadence", "{", 400],
      ["PATCH", "/subscriber/reminder-cadence", J({ thresholds: [999] }), 400],
    ], ["PATCH", "/subscriber/reminder-cadence", J({ thresholds: null }), 200]);
  });

  it("notification mode", async () => {
    const s = await subscriber();
    await assertSite(s.cookie, s.email, "subscriber_notification_mode", [
      ["PATCH", "/subscriber/notification-mode", "{", 400],
      ["PATCH", "/subscriber/notification-mode", J({ mode: "never" }), 400],
    ], ["PATCH", "/subscriber/notification-mode", J({ mode: store.NOTIFICATION_MODE_DIGEST }), 200]);
  });

  it("PTIN", async () => {
    const s = await subscriber();
    await assertSite(s.cookie, s.email, "subscriber_ptin_set", [
      ["PATCH", "/subscriber/ptin", "{", 400],
      ["PATCH", "/subscriber/ptin", J({ enabled: "yes" }), 400],
    ], ["PATCH", "/subscriber/ptin", J({ enabled: true }), 404]);
  });

  it("phone start: the 24-hour bucket takes no free spend", async () => {
    extraEnv = { TWILIO_ACCOUNT_SID: "AC", TWILIO_AUTH_TOKEN: "t", TWILIO_FROM_NUMBER: "+15550000000" };
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("{}", { status: 201 }));
    const s = await subscriber();
    await assertSite(s.cookie, s.email, "subscriber_phone_start_verification", [
      ["POST", "/subscriber/phone/start-verification", OVERSIZED, 400],
      ["POST", "/subscriber/phone/start-verification", "{", 400],
      ["POST", "/subscriber/phone/start-verification", J({ phone_number: "nope" }), 400],
      ["POST", "/subscriber/phone/start-verification", J({ phone_number: "+15551234567" }), 400],
    ], ["POST", "/subscriber/phone/start-verification", J({ phone_number: "+15551234567", consent: true, consent_version: "v1" }), 200]);
  });

  it("phone confirm: a wrong code stays metered", async () => {
    const s = await subscriber();
    await assertSite(s.cookie, s.email, "subscriber_phone_confirm_verification", [
      ["POST", "/subscriber/phone/confirm-verification", OVERSIZED, 400],
      ["POST", "/subscriber/phone/confirm-verification", "{", 400],
      ["POST", "/subscriber/phone/confirm-verification", J({ code: "" }), 400],
    ], ["POST", "/subscriber/phone/confirm-verification", J({ code: "123456" }), 400]);
  });

  it("CPE: shape refusals and a bogus line id are free", async () => {
    const f = await firm();
    const s = await subscriber();
    const id = await license(f, s.email);
    const good = { subscriber_id: id, entry_date: "2026-01-02", hours: "1", category: "general" };
    await assertSite(s.cookie, s.email, "subscriber_cpe_create", [
      ["POST", "/subscriber/cpe", "{", 400],
      ["POST", "/subscriber/cpe", J({ ...good, description: "a\u0007" }), 400],
      ["POST", "/subscriber/cpe", J({ ...good, subscriber_id: "" }), 400],
      ["POST", "/subscriber/cpe", J({ ...good, entry_date: "nope" }), 400],
      ["POST", "/subscriber/cpe", J({ ...good, hours: "0" }), 400],
      ["POST", "/subscriber/cpe", J({ ...good, category: "nope" }), 400],
      ["POST", "/subscriber/cpe", J({ ...good, subscriber_id: BOGUS }), 404],
    ], ["POST", "/subscriber/cpe", J(good), 201]);
  });
});

describe("SEC-71: firm settings", () => {
  it("peer review, and a null clear still writes", async () => {
    const f = await firm();
    await assertSite(f.cookie, f.firmId, "firm_peer_review_set", [
      ["PATCH", "/firm/peer-review", OVERSIZED, 400],
      ["PATCH", "/firm/peer-review", "{", 400],
      ["PATCH", "/firm/peer-review", J({ due_date: "nope" }), 400],
    ], ["PATCH", "/firm/peer-review", J({ due_date: null }), 200]);
    await assertSite(f.cookie, f.firmId, "firm_peer_review_set", [], ["PATCH", "/firm/peer-review", J({ due_date: "2027-01-01" }), 200]);
  });

  it("reply-to", async () => {
    const f = await firm();
    await assertSite(f.cookie, f.firmId, "firm_reply_to_set", [
      ["PATCH", "/firm/reply-to", "{", 400],
      ["PATCH", "/firm/reply-to", J({ email: "nope" }), 400],
    ], ["PATCH", "/firm/reply-to", J({ email: null }), 200]);
    await assertSite(f.cookie, f.firmId, "firm_reply_to_set", [], ["PATCH", "/firm/reply-to", J({ email: uniqueEmail("reply") }), 200]);
  });

  it("reminder cadence", async () => {
    const f = await firm();
    await assertSite(f.cookie, f.firmId, "firm_reminder_cadence_set", [
      ["PATCH", "/firm/reminder-cadence", "{", 400],
      ["PATCH", "/firm/reminder-cadence", J({ thresholds: [999] }), 400],
    ], ["PATCH", "/firm/reminder-cadence", J({ thresholds: null }), 200]);
    await assertSite(f.cookie, f.firmId, "firm_reminder_cadence_set", [], ["PATCH", "/firm/reminder-cadence", J({ thresholds: [30, 7] }), 200]);
  });

  it("rule-change alerts and admin digest", async () => {
    const f = await firm();
    for (const [path, bucket] of [["/firm/rule-change-alerts", "firm_rule_change_alerts_set"], ["/firm/admin-digest", "firm_admin_digest_set"]] as const) {
      await assertSite(f.cookie, f.firmId, bucket, [
        ["PATCH", path, "{", 400],
        ["PATCH", path, J({ enabled: "yes" }), 400],
      ], ["PATCH", path, J({ enabled: false }), 200]);
    }
  });

  it("Teams: a bad URL is free, a clear still writes", async () => {
    const f = await firm();
    await assertSite(f.cookie, f.firmId, "firm_teams_webhook_set", [
      ["PATCH", "/firm/integrations/teams", "{", 400],
      ["PATCH", "/firm/integrations/teams", J({ webhook_url: "https://evil.example/hook" }), 400],
    ], ["PATCH", "/firm/integrations/teams", J({ webhook_url: null }), 200]);
  });

  it("NPS and the questionnaire", async () => {
    const f = await firm();
    await assertSite(f.cookie, f.firmId, "firm_nps", [
      ["POST", "/firm/nps", "{", 400],
      ["POST", "/firm/nps", J({ score: 11 }), 400],
    ], ["POST", "/firm/nps", J({ score: 7 }), 200]);
    await assertSite(f.cookie, f.firmId, "firm_questionnaire_submit", [
      ["POST", "/firm/questionnaire", "{", 400],
      ["POST", "/firm/questionnaire", J({ selected_features: [7] }), 400],
    ], ["POST", "/firm/questionnaire", J({ selected_features: ["a"] }), 200]);
  });

  it("checkout: shape refusals are free and the slot still guards Stripe", async () => {
    extraEnv = { STRIPE_SECRET_KEY: "sk_test_x", STRIPE_PRICE_FIRM_SCALE: "price_x" };
    const spy = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(J({ id: "cs_x", url: "https://stripe.example/x" }), { status: 200 }));
    const f = await firm();
    const before = await slots(f.firmId, "firm_billing_checkout");
    for (const [body, type] of [["{}", null], ["{", "application/json"], [J({ tier: "nope" }), "application/json"], [J({ tier: "firm_scale", interval: "weekly" }), "application/json"]] as const) {
      expect((await send("POST", "/firm/billing/checkout", f.cookie, body, type)).status).toBe(400);
    }
    expect(await slots(f.firmId, "firm_billing_checkout")).toBe(before);
    expect(spy).not.toHaveBeenCalled();
    await send("POST", "/firm/billing/checkout", f.cookie, J({ tier: "firm_scale" }));
    expect(await slots(f.firmId, "firm_billing_checkout")).toBe(before + 1);
    expect(spy).toHaveBeenCalled();
  });
});

describe("SEC-71: roster routes and their ids", () => {
  it("create: shape refusals are free, and the dedupe oracle stays metered", async () => {
    const f = await firm();
    const email = uniqueEmail("dupe");
    const good = { staff_label: "Staff", email, state_slug: "georgia", license_type_id: "ga-individual" };
    await assertSite(f.cookie, f.firmId, "firm_license_create", [
      ["POST", "/firm/licenses", "{", 400],
      ["POST", "/firm/licenses", J({ ...good, staff_label: "a\u0007" }), 400],
      ["POST", "/firm/licenses", J({ ...good, email: "nope" }), 400],
      ["POST", "/firm/licenses", J({ ...good, state_slug: "nowhere" }), 400],
      ["POST", "/firm/licenses", J({ ...good, license_issue_date: "nope" }), 400],
      ["POST", "/firm/licenses", J({ ...good, renewal_fee: "nope" }), 400],
    ], ["POST", "/firm/licenses", J(good), 201]);
    await assertSite(f.cookie, f.firmId, "firm_license_create", [], ["POST", "/firm/licenses", J(good), 409]);
  });

  it("patch: a bogus id and shape refusals are free, and the dedupe oracle stays metered", async () => {
    const f = await firm();
    const id = await license(f);
    const taken = uniqueEmail("taken");
    await license(f, taken);
    await assertSite(f.cookie, f.firmId, "firm_license_patch", [
      ["PATCH", `/firm/licenses/${BOGUS}`, J({ staff_label: "X" }), 404],
      ["PATCH", `/firm/licenses/${id}`, "{", 400],
      ["PATCH", `/firm/licenses/${id}`, J({ staff_label: "a\u0007" }), 400],
      ["PATCH", `/firm/licenses/${id}`, J({ email: "nope" }), 400],
    ], ["PATCH", `/firm/licenses/${id}`, J({ staff_label: "Renamed" }), 200]);
    await assertSite(f.cookie, f.firmId, "firm_license_patch", [], ["PATCH", `/firm/licenses/${id}`, J({ email: taken }), 409]);
  });

  it("delete and renew: a bogus id is free", async () => {
    const f = await firm();
    const id = await license(f);
    await assertSite(f.cookie, f.firmId, "firm_license_renew", [["POST", `/firm/licenses/${BOGUS}/renew`, null, 404, null]], ["POST", `/firm/licenses/${id}/renew`, null, 200, null]);
    await assertSite(f.cookie, f.firmId, "firm_license_delete", [["DELETE", `/firm/licenses/${BOGUS}`, null, 404, null]], ["DELETE", `/firm/licenses/${id}`, null, 200, null]);
  });

  it("staff reminder and rule-change notify", async () => {
    const f = await firm();
    const id = await license(f);
    await assertSite(f.cookie, f.firmId, "firm_staff_cpe_reminder", [
      ["POST", "/firm/staff-cpe-reminder", "{", 400],
      ["POST", "/firm/staff-cpe-reminder", J({}), 400],
      ["POST", "/firm/staff-cpe-reminder", J({ subscriber_id: BOGUS }), 404],
    ], ["POST", "/firm/staff-cpe-reminder", J({ subscriber_id: id }), 200]);
    const rule = { state_slug: "georgia", jurisdiction: "Georgia", summary: "A change.", effective_date_label: "Jan 1, 2027" };
    await assertSite(f.cookie, f.firmId, "firm_rule_change_notify", [
      ["POST", "/firm/rule-change/notify", "{", 400],
      ["POST", "/firm/rule-change/notify", J({}), 400],
      ["POST", "/firm/rule-change/notify", J({ ...rule, summary: "a\u0007" }), 400],
    ], ["POST", "/firm/rule-change/notify", J(rule), 200]);
  });

  it("CPE create: a bogus id costs nothing and a group spends one slot per line", async () => {
    const f = await firm();
    const email = uniqueEmail("cpe");
    const primary = await license(f, email);
    const second = await license(f, email, "illinois", "il-individual");
    const stranger = await license(f);
    const good = { subscriber_id: primary, entry_date: "2026-01-02", hours: "1", category: "general" };
    await assertSite(f.cookie, f.firmId, "cpe_entry_create", [
      ["POST", "/firm/cpe", "{", 400],
      ["POST", "/firm/cpe", J({ ...good, hours: "0" }), 400],
      ["POST", "/firm/cpe", J({ ...good, document_id: BOGUS }), 400],
      ["POST", "/firm/cpe", J({ ...good, subscriber_id: BOGUS }), 404],
      ["POST", "/firm/cpe", J({ ...good, also: [{ subscriber_id: BOGUS }] }), 404],
      ["POST", "/firm/cpe", J({ ...good, also: [{ subscriber_id: stranger }] }), 400],
      ["POST", "/firm/cpe", J({ ...good, also: [{ subscriber_id: primary }] }), 400],
    ], ["POST", "/firm/cpe", J(good), 201]);
    const before = await slots(f.firmId, "cpe_entry_create");
    expect((await send("POST", "/firm/cpe", f.cookie, J({ ...good, also: [{ subscriber_id: second }] }))).status).toBe(201);
    expect(await slots(f.firmId, "cpe_entry_create")).toBe(before + 2);
  });

  it("CPE delete, both scopes", async () => {
    const f = await firm();
    const id = await license(f);
    const entry = await send("POST", "/firm/cpe", f.cookie, J({ subscriber_id: id, entry_date: "2026-01-02", hours: "1", category: "general" }));
    const entryId = (await entry.json<{ id: string }>()).id;
    await assertSite(f.cookie, f.firmId, "cpe_entry_delete", [
      ["DELETE", `/firm/cpe/${BOGUS}`, null, 404, null],
      ["DELETE", `/firm/cpe/${BOGUS}?scope=group`, null, 404, null],
    ], ["DELETE", `/firm/cpe/${entryId}`, null, 200, null]);
    await assertSite(f.cookie, f.firmId, "cpe_entry_delete", [["DELETE", `/firm/cpe/${entryId}`, null, 404, null]], ["DELETE", `/firm/cpe/${(await (await send("POST", "/firm/cpe", f.cookie, J({ subscriber_id: id, entry_date: "2026-01-03", hours: "1", category: "general" }))).json<{ id: string }>()).id}?scope=group`, null, 200, null]);
  });

  it("checklist: create, update and delete", async () => {
    const f = await firm();
    const id = await license(f);
    await assertSite(f.cookie, f.firmId, "firm_checklist_write", [
      ["POST", `/firm/licenses/${BOGUS}/checklist`, J({ label: "x" }), 404],
      ["POST", `/firm/licenses/${id}/checklist`, "{", 400],
      ["POST", `/firm/licenses/${id}/checklist`, J({}), 400],
    ], ["POST", `/firm/licenses/${id}/checklist`, J({ label: "x" }), 201]);
    const list = await send("GET", `/firm/licenses/${id}/checklist`, f.cookie, null, null);
    const itemId = (await list.json<{ items: Array<{ id: string }> }>()).items[0]!.id;
    await assertSite(f.cookie, f.firmId, "firm_checklist_write", [
      ["PATCH", `/firm/checklist/${BOGUS}`, J({ label: "y" }), 404],
      ["PATCH", `/firm/checklist/${itemId}`, "{", 400],
      ["PATCH", `/firm/checklist/${itemId}`, J({ status: "nope" }), 400],
      ["PATCH", `/firm/checklist/${itemId}`, J({ document_id: BOGUS }), 404],
    ], ["PATCH", `/firm/checklist/${itemId}`, J({ label: "y" }), 200]);
    await assertSite(f.cookie, f.firmId, "firm_checklist_write", [["DELETE", `/firm/checklist/${BOGUS}`, null, 404, null]], ["DELETE", `/firm/checklist/${itemId}`, null, 200, null]);
  });

  it("attestation", async () => {
    const f = await firm();
    const id = await license(f);
    await assertSite(f.cookie, f.firmId, "firm_attestation_create", [
      ["POST", `/firm/licenses/${BOGUS}/attestations`, J({ signature_text: "Ann Partner" }), 404],
      ["POST", `/firm/licenses/${id}/attestations`, "{", 400],
      ["POST", `/firm/licenses/${id}/attestations`, J({ signature_text: 7 }), 400],
      ["POST", `/firm/licenses/${id}/attestations`, J({ signature_text: "A" }), 400],
    ], ["POST", `/firm/licenses/${id}/attestations`, J({ signature_text: "Ann Partner" }), 201]);
  });

  it("SSO unlink: a bogus id is free, and a real unlink still works", async () => {
    const f = await firm();
    const linked = await store.linkOauthIdentity(env.DB, { firmId: f.firmId, provider: "google", providerSubject: `sub-${Date.now()}-${seq}`, providerEmail: uniqueEmail("sso") });
    await assertSite(f.cookie, f.firmId, "oauth_identity_delete", [["DELETE", `/firm/oauth-identities/${BOGUS}`, null, 404, null]], ["DELETE", `/firm/oauth-identities/${linked!.id}`, null, 200, null]);
  });
});

describe("SEC-71: documents", () => {
  function multipart(fields: Record<string, string | File>): { body: FormData } {
    const form = new FormData();
    for (const [k, v] of Object.entries(fields)) form.append(k, v as never);
    return { body: form };
  }
  async function post(path: string, cookie: string, form: FormData): Promise<Response> {
    return workerFetch(new Request(`${BASE}${path}`, { method: "POST", headers: { "cf-connecting-ip": uniqueIp(), Cookie: cookie, Origin: BASE }, body: form }));
  }
  const pdf = (bytes = 10) => new File([new Uint8Array(bytes)], "c.pdf", { type: "application/pdf" });

  it("upload: a bogus id and every shape refusal are free, delete too", async () => {
    const f = await firm();
    const id = await license(f);
    const path = `/firm/licenses/${id}/documents`;
    const bucket = "firm_document_upload";
    const before = await slots(f.firmId, bucket);
    expect((await post(`/firm/licenses/${BOGUS}/documents`, f.cookie, multipart({ file: pdf(), kind: "cpe" }).body)).status).toBe(404);
    expect((await send("POST", path, f.cookie, "not multipart", "text/plain")).status).toBe(400);
    expect((await post(path, f.cookie, multipart({ kind: "cpe" }).body)).status).toBe(400);
    expect((await post(path, f.cookie, multipart({ file: pdf() }).body)).status).toBe(400);
    expect((await post(path, f.cookie, multipart({ file: new File(["x"], "c.exe", { type: "application/x-msdownload" }), kind: "cpe" }).body)).status).toBe(400);
    expect((await post(path, f.cookie, multipart({ file: pdf(store.DOCUMENT_MAX_FILE_BYTES + 1), kind: "cpe" }).body)).status).toBe(400);
    expect(await slots(f.firmId, bucket)).toBe(before);

    const ok = await post(path, f.cookie, multipart({ file: pdf(), kind: "cpe" }).body);
    expect(ok.status).toBe(201);
    expect(await slots(f.firmId, bucket)).toBe(before + 1);

    const docId = (await ok.json<{ document: { id: string } }>()).document.id;
    await assertSite(f.cookie, f.firmId, "firm_document_delete", [["DELETE", `/firm/documents/${BOGUS}`, null, 404, null]], ["DELETE", `/firm/documents/${docId}`, null, 200, null]);
  });

  it("a body declaring itself past the limit is refused unparsed, and a 2MB file still fits", async () => {
    const f = await firm();
    const id = await license(f);
    const declared = await workerFetch(new Request(`${BASE}/firm/licenses/${id}/documents`, {
      method: "POST",
      headers: { "cf-connecting-ip": uniqueIp(), Cookie: f.cookie, Origin: BASE, "content-type": "multipart/form-data; boundary=x", "content-length": String(store.DOCUMENT_MAX_FILE_BYTES + 70_000) },
      body: "--x--",
    }));
    expect(declared.status).toBe(400);
    expect(await slots(f.firmId, "firm_document_upload")).toBe(0);
    expect((await post(`/firm/licenses/${id}/documents`, f.cookie, multipart({ file: pdf(store.DOCUMENT_MAX_FILE_BYTES), kind: "cpe" }).body)).status).toBe(201);
  });

  it("extract: both buckets take no free spend, and a real upload spends both", async () => {
    const f = await firm();
    const id = await license(f);
    const path = `/firm/licenses/${id}/documents/extract`;
    expect((await post(`/firm/licenses/${BOGUS}/documents/extract`, f.cookie, multipart({ file: pdf() }).body)).status).toBe(404);
    expect((await send("POST", path, f.cookie, "not multipart", "text/plain")).status).toBe(400);
    expect((await post(path, f.cookie, multipart({ note: "no file" }).body)).status).toBe(400);
    expect(await slots(f.firmId, "firm_document_upload")).toBe(0);
    expect(await slots(f.firmId, "firm_document_extract")).toBe(0);
    const ok = await post(path, f.cookie, multipart({ file: pdf() }).body);
    expect(ok.status).toBeLessThan(300);
    expect(await slots(f.firmId, "firm_document_upload")).toBe(1);
    expect(await slots(f.firmId, "firm_document_extract")).toBe(1);
  });
});

describe("SEC-71: mobility and Slack", () => {
  it("single, batch and roster checks: an unknown state or service type is free", async () => {
    const f = await firm();
    const home = { home_state_slug: "colorado", target_state_slug: "texas", service_type: "tax" };
    const spent = async () => (await slots(f.firmId, "mobility_check")) + (await slots(f.firmId, "mobility_check_unmetered")) + (await slots(f.firmId, "mobility_check_batch"));
    for (const [path, body] of [
      ["/firm/mobility/check", { ...home, home_state_slug: "nowhere" }],
      ["/firm/mobility/check", { ...home, target_state_slug: "nowhere" }],
      ["/firm/mobility/check", { ...home, service_type: "nope" }],
      ["/firm/mobility/check-batch", { ...home, home_state_slug: "nowhere" }],
      ["/firm/mobility/check-batch", { ...home, service_type: "nope" }],
    ] as const) {
      expect((await send("POST", path, f.cookie, J(body))).status).toBe(400);
    }
    expect(await spent()).toBe(0);
    expect((await send("POST", "/firm/mobility/check", f.cookie, J(home))).status).toBe(200);
    expect(await spent()).toBe(1);
    expect((await send("POST", "/firm/mobility/check-batch", f.cookie, J(home))).status).toBe(200);
    expect(await spent()).toBe(2);
    await assertSite(f.cookie, f.firmId, "mobility_check_roster", [
      ["POST", "/firm/mobility/check-roster", J({ target_state_slug: "nowhere", service_type: "tax" }), 400],
      ["POST", "/firm/mobility/check-roster", J({ target_state_slug: "texas", service_type: "nope" }), 400],
    ], ["POST", "/firm/mobility/check-roster", J({ target_state_slug: "texas", service_type: "tax" }), 200]);
  });

  it("completions: a bogus id is free", async () => {
    const f = await firm();
    const id = await license(f);
    const good = { subscriber_id: id, target_state_slug: "texas", service_type: "tax" };
    await assertSite(f.cookie, f.firmId, "mobility_completion_create", [
      ["POST", "/firm/mobility/completions", "{", 400],
      ["POST", "/firm/mobility/completions", J({ ...good, subscriber_id: "" }), 400],
      ["POST", "/firm/mobility/completions", J({ ...good, target_state_slug: "nowhere" }), 400],
      ["POST", "/firm/mobility/completions", J({ ...good, service_type: "nope" }), 400],
      ["POST", "/firm/mobility/completions", J({ ...good, subscriber_id: BOGUS }), 404],
    ], ["POST", "/firm/mobility/completions", J(good), 201]);
    const row = await env.DB.prepare("SELECT id FROM mobility_completions WHERE firm_id = ?1").bind(f.firmId).first<{ id: string }>();
    await assertSite(f.cookie, f.firmId, "mobility_completion_delete", [["DELETE", `/firm/mobility/completions/${BOGUS}`, null, 404, null]], ["DELETE", `/firm/mobility/completions/${row!.id}`, null, 200, null]);
  });

  it("Slack callback: a declined or empty callback is free, a state guess stays metered", async () => {
    extraEnv = { SLACK_OAUTH_CLIENT_ID: "id", SLACK_OAUTH_CLIENT_SECRET: "secret" };
    const f = await firm();
    await assertSite(f.cookie, f.firmId, "firm_slack_connect", [
      ["GET", "/firm/integrations/slack/callback?error=access_denied", null, 302, null],
      ["GET", "/firm/integrations/slack/callback", null, 302, null],
    ], ["GET", "/firm/integrations/slack/callback?code=c&state=guess", null, 302, null]);
  });
});

/**
 * A static check, so a new route can't reintroduce this by parsing a body under a
 * victim-keyed limiter. It can't see a 404 on a request-supplied id; the cases
 * above cover those for the routes that exist today.
 */
const PARSE_MARKERS = ["request.text()", "request.json()", "request.formData()", "readFirmLicenseJsonBody(", "JSON.parse(raw"];
const VICTIM_KEY = /checkRateLimit\(\s*env\.DB,\s*session\.(firmId|memberId|emailNormalized)\b/g;
// Its body block swallows malformed input, so there is no refusal to spend on.
const ALLOWED = new Set(["handleFirmAccountDelete"]);

const FUNCTION_BODY = /^(?:export )?(?:async )?function (\w+)\([\s\S]*?^\}$/gm;

function functionsParsingBelowALimiter(source: string): string[] {
  const flagged: string[] = [];
  for (const match of source.matchAll(FUNCTION_BODY)) {
    const body = match[0];
    // The first limiter: a parse between two limiters still sits below one (SEC-75).
    const first = [...body.matchAll(VICTIM_KEY)][0]?.index;
    if (first === undefined) continue;
    const below = body.slice(first);
    if (PARSE_MARKERS.some((m) => below.includes(m))) flagged.push(match[1] as string);
  }
  return flagged;
}

describe("census: no victim-keyed limiter sits above a body parse", () => {
  it("the matcher fires on a synthetic handler", () => {
    const planted = 'async function handlePlanted() {\n  const a = await checkRateLimit(env.DB, session.firmId, "x", Y);\n  const raw = await request.text();\n}\n';
    expect(functionsParsingBelowALimiter(planted)).toEqual(["handlePlanted"]);
    const clean = 'async function handleClean() {\n  const raw = await request.text();\n  const a = await checkRateLimit(env.DB, session.firmId, "x", Y);\n}\n';
    expect(functionsParsingBelowALimiter(clean)).toEqual([]);
    const between = 'async function handleBetween() {\n  const a = await checkRateLimit(env.DB, session.firmId, "x", Y);\n  const raw = await request.text();\n  const b = await checkRateLimit(env.DB, session.firmId, "z", Y);\n}\n';
    expect(functionsParsingBelowALimiter(between)).toEqual(["handleBetween"]);
  });

  it("the file has limiters to scan", () => {
    expect([...indexSource.matchAll(VICTIM_KEY)].length).toBeGreaterThan(50);
  });

  it("every limiter in the file sits in a function the matcher reads", () => {
    const inFile = [...indexSource.matchAll(VICTIM_KEY)].length;
    const inBodies = [...indexSource.matchAll(FUNCTION_BODY)].reduce((n, m) => n + [...m[0].matchAll(VICTIM_KEY)].length, 0);
    expect(inBodies).toBe(inFile);
  });

  it("index.ts is clean", () => {
    expect(functionsParsingBelowALimiter(indexSource).filter((name) => !ALLOWED.has(name))).toEqual([]);
  });
});
