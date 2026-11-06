/**
 * SEC-70 + SEC-71 (LOW, 2026-11-06): checkRateLimit writes its row when called,
 * whatever the request goes on to do. A limiter above a refusal the caller can
 * trigger for free is a free way to fill the bucket, and a firmId key lets any
 * member fill it for the whole firm.
 *
 * Each free arm asserts rate_limit_hits stays empty, and each site has a control
 * showing a well-formed request does write a row, so the zeros mean something.
 */
import { env } from "cloudflare:test";
import { describe, expect, it } from "vitest";
import * as store from "../src/store";
import { hashPassword } from "../src/password";
import { RATE_LIMIT_FIRM_PACKET_EXPORT, RATE_LIMIT_FIRM_SESSION_REVOKE } from "../src/validation";

const BASE = "https://deadline-radar.com";
const PASSWORD = "correct horse battery staple 70";
const KEY = btoa(String.fromCharCode(...crypto.getRandomValues(new Uint8Array(32))));
const BOGUS = "00000000-0000-4000-8000-000000000000";

async function workerFetch(request: Request): Promise<Response> {
  const worker = (await import("../src/index")).default;
  const ctx = { waitUntil() {}, passThroughOnException() {}, props: {} } as unknown as ExecutionContext;
  return worker.fetch(request, { ...env, TOTP_ENCRYPTION_KEY: KEY } as never, ctx);
}

let seq = 0;
function uniqueEmail(label: string): string {
  seq += 1;
  return `sec70-${label}-${Date.now()}-${seq}@examplefirm.com`;
}
function uniqueIp(): string {
  seq += 1;
  return `198.51.${Math.floor(seq / 250) + 100}.${(seq % 250) + 1}`;
}

async function firm() {
  const f = await store.createFirm(env.DB, { name: "SEC-70 LLP", adminEmail: uniqueEmail("owner") });
  await env.DB.prepare("UPDATE firms SET plan_tier = 'firm_starter' WHERE id = ?1").bind(f.id).run();
  await store.setFirmMemberPassword(env.DB, f.memberId, await hashPassword(PASSWORD));
  const { rawSessionToken } = await store.createSession(env.DB, f.id, f.memberId);
  const verified = await store.verifySession(env.DB, rawSessionToken);
  if (!verified) throw new Error("fresh session does not verify");
  return { firmId: f.id, memberId: f.memberId, sessionId: verified.sessionId, cookie: `dr_firm_session=${rawSessionToken}` };
}

async function member(firmId: string, role: "partner" | "staff") {
  const created = await store.createFirmMember(env.DB, { firmId, email: uniqueEmail(role), role });
  await store.markFirmMemberJoined(env.DB, created.id);
  const { rawSessionToken } = await store.createSession(env.DB, firmId, created.id);
  const verified = await store.verifySession(env.DB, rawSessionToken);
  if (!verified) throw new Error("member session does not verify");
  return { id: created.id, sessionId: verified.sessionId, cookie: `dr_firm_session=${rawSessionToken}` };
}

function send(method: string, path: string, cookie: string, body: string | null, contentType: string | null = "application/json"): Promise<Response> {
  const headers: Record<string, string> = { "cf-connecting-ip": uniqueIp(), Cookie: cookie, Origin: BASE };
  if (contentType) headers["content-type"] = contentType;
  return workerFetch(new Request(`${BASE}${path}`, { method, headers, body }));
}

async function slots(key: string, bucket: string): Promise<number> {
  const row = await env.DB.prepare("SELECT COUNT(*) AS n FROM rate_limit_hits WHERE ip = ?1 AND bucket = ?2").bind(key, bucket).first<{ n: number }>();
  return row?.n ?? 0;
}

const OVERSIZED = JSON.stringify({ pad: "x".repeat(9000) });

describe("SEC-70: a refusal the caller triggers for free spends no slot", () => {
  it("2FA enroll: oversized, unparseable and non-object bodies, then the control", async () => {
    const f = await firm();
    for (const body of [OVERSIZED, "{", "null", "[]", "7"]) {
      expect((await send("POST", "/firm/2fa/enroll", f.cookie, body)).status).toBe(400);
    }
    expect(await slots(f.memberId, "firm_2fa_enroll")).toBe(0);
    expect((await send("POST", "/firm/2fa/enroll", f.cookie, JSON.stringify({ current_password: "wrong" }))).status).toBe(400);
    expect(await slots(f.memberId, "firm_2fa_enroll")).toBe(1);
  });

  it("2FA enroll: nine junk posts leave the owner able to enroll", async () => {
    const f = await firm();
    for (let i = 0; i < 9; i++) await send("POST", "/firm/2fa/enroll", f.cookie, "{");
    expect((await send("POST", "/firm/2fa/enroll", f.cookie, JSON.stringify({ current_password: PASSWORD }))).status).toBe(200);
  });

  it("2FA disable: every shape refusal is free, and a real attempt still meets the limiter", async () => {
    const f = await firm();
    const cases: Array<[string | null, string | null]> = [["{}", null], ["", "application/json"], [OVERSIZED, "application/json"], ["{", "application/json"], ["[]", "application/json"], ["{}", "application/json"]];
    for (const [body, type] of cases) {
      expect((await send("POST", "/firm/2fa/disable", f.cookie, body, type)).status).toBe(400);
    }
    expect(await slots(f.memberId, "firm_2fa_disable")).toBe(0);
    await send("POST", "/firm/2fa/disable", f.cookie, JSON.stringify({ code: "123456" }));
    expect(await slots(f.memberId, "firm_2fa_disable")).toBe(1);
  });

  it("change email: content type, shape and address refusals are free", async () => {
    const f = await firm();
    const staff = await member(f.firmId, "staff");
    expect((await send("POST", "/firm/change-email", staff.cookie, "{}", null)).status).toBe(400);
    expect((await send("POST", "/firm/change-email", staff.cookie, "{")).status).toBe(400);
    expect((await send("POST", "/firm/change-email", staff.cookie, JSON.stringify({ new_email: "nope" }))).status).toBe(400);
    expect(await slots(f.firmId, "firm_change_email")).toBe(0);
    await send("POST", "/firm/change-email", staff.cookie, JSON.stringify({ new_email: uniqueEmail("new"), current_password: "wrong" }));
    expect(await slots(f.firmId, "firm_change_email")).toBe(1);
  });

  it("member invite: shape, address and role refusals are free", async () => {
    const f = await firm();
    for (const body of [OVERSIZED, "{", JSON.stringify({ email: "nope", role: "staff" }), JSON.stringify({ email: uniqueEmail("x"), role: "boss" })]) {
      expect((await send("POST", "/firm/members/invite", f.cookie, body)).status).toBe(400);
    }
    expect(await slots(f.firmId, "firm_member_invite")).toBe(0);
    await send("POST", "/firm/members/invite", f.cookie, JSON.stringify({ email: uniqueEmail("x"), role: "staff", current_password: "wrong" }));
    expect(await slots(f.firmId, "firm_member_invite")).toBe(1);
  });

  it("role change: a bogus id, a bad role and the last-Partner refusal are free", async () => {
    const f = await firm();
    expect((await send("PATCH", `/firm/members/${BOGUS}`, f.cookie, JSON.stringify({ role: "staff" }))).status).toBe(404);
    const staff = await member(f.firmId, "staff");
    expect((await send("PATCH", `/firm/members/${staff.id}`, f.cookie, JSON.stringify({ role: "boss" }))).status).toBe(400);
    expect((await send("PATCH", `/firm/members/${f.memberId}`, f.cookie, JSON.stringify({ role: "staff" }))).status).toBe(400);
    expect(await slots(f.firmId, "firm_member_role_change")).toBe(0);
    await send("PATCH", `/firm/members/${staff.id}`, f.cookie, JSON.stringify({ role: "office_manager", current_password: "wrong" }));
    expect(await slots(f.firmId, "firm_member_role_change")).toBe(1);
  });

  it("member remove: a bogus id and the primary-contact refusal are free", async () => {
    const f = await firm();
    expect((await send("DELETE", `/firm/members/${BOGUS}`, f.cookie, "{}")).status).toBe(404);
    expect((await send("DELETE", `/firm/members/${f.memberId}`, f.cookie, "{}")).status).toBe(400);
    expect(await slots(f.firmId, "firm_member_remove")).toBe(0);
    const staff = await member(f.firmId, "staff");
    await send("DELETE", `/firm/members/${staff.id}`, f.cookie, JSON.stringify({ current_password: "wrong" }));
    expect(await slots(f.firmId, "firm_member_remove")).toBe(1);
  });

  it("make primary: a bogus id and a non-Partner target are free", async () => {
    const f = await firm();
    expect((await send("POST", `/firm/members/${BOGUS}/make-primary`, f.cookie, "{}")).status).toBe(404);
    const staff = await member(f.firmId, "staff");
    expect((await send("POST", `/firm/members/${staff.id}/make-primary`, f.cookie, "{}")).status).toBe(400);
    expect(await slots(f.firmId, "firm_member_make_primary")).toBe(0);
    const partner = await member(f.firmId, "partner");
    await send("POST", `/firm/members/${partner.id}/make-primary`, f.cookie, JSON.stringify({ current_password: "wrong" }));
    expect(await slots(f.firmId, "firm_member_make_primary")).toBe(1);
  });
});

describe("SEC-71: Staff can't veto a firm-wide function for free", () => {
  it("packet export: one past the cap of bogus ids from Staff is still 404, nothing written", async () => {
    const f = await firm();
    const staff = await member(f.firmId, "staff");
    for (let i = 0; i <= RATE_LIMIT_FIRM_PACKET_EXPORT.max; i++) {
      expect((await send("GET", `/firm/licenses/${BOGUS}/packet-export`, staff.cookie, null, null)).status).toBe(404);
    }
    expect(await slots(f.firmId, "firm_packet_export")).toBe(0);

    const created = await send(
      "POST",
      "/firm/licenses",
      f.cookie,
      JSON.stringify({ staff_label: "Export Staff", email: uniqueEmail("licensee"), state_slug: "georgia", license_type_id: "ga-individual" })
    );
    expect(created.status).toBe(201);
    const { id } = await created.json<{ id: string }>();
    expect((await send("GET", `/firm/licenses/${id}/packet-export`, f.cookie, null, null)).status).toBe(200);
    expect(await slots(f.firmId, "firm_packet_export")).toBe(1);
  });

  it("testimonial: an empty quote from Staff is free, a real one is metered", async () => {
    const f = await firm();
    const staff = await member(f.firmId, "staff");
    for (const body of [OVERSIZED, "{", JSON.stringify({ quote_text: "" })]) {
      expect((await send("POST", "/firm/testimonial", staff.cookie, body)).status).toBe(400);
    }
    expect(await slots(f.firmId, "firm_testimonial")).toBe(0);
    expect((await send("POST", "/firm/testimonial", staff.cookie, JSON.stringify({ quote_text: "Useful." }))).status).toBe(200);
    expect(await slots(f.firmId, "firm_testimonial")).toBe(1);
  });

  it("firm mobility check: bad bodies and unknown states from Staff are free, a real check is metered", async () => {
    const f = await firm();
    const staff = await member(f.firmId, "staff");
    for (const body of ["", OVERSIZED, "{", "[]", JSON.stringify({ firm_home_state_slug: "nowhere", target_state_slug: "texas" })]) {
      expect((await send("POST", "/firm/mobility/firm-check", staff.cookie, body)).status).toBe(400);
    }
    expect(await slots(f.firmId, "firm_mobility_check")).toBe(0);
    const ok = await send("POST", "/firm/mobility/firm-check", staff.cookie, JSON.stringify({ firm_home_state_slug: "colorado", target_state_slug: "texas" }));
    expect(ok.status).toBe(200);
    expect(await slots(f.firmId, "firm_mobility_check")).toBe(1);
  });
});

describe("SEC-71: nobody else can drain a session revoke budget", () => {
  it("another member's bogus revokes leave the Partner's revoke working", async () => {
    const f = await firm();
    const staff = await member(f.firmId, "staff");
    for (let i = 0; i < RATE_LIMIT_FIRM_SESSION_REVOKE.max; i++) {
      expect((await send("DELETE", `/firm/sessions/${BOGUS}`, staff.cookie, null, null)).status).toBe(404);
    }
    expect((await send("DELETE", `/firm/sessions/${BOGUS}`, staff.cookie, null, null)).status).toBe(429);

    const other = await store.createSession(env.DB, f.firmId, f.memberId);
    const otherSession = await store.verifySession(env.DB, other.rawSessionToken);
    expect((await send("DELETE", `/firm/sessions/${otherSession!.sessionId}`, f.cookie, null, null)).status).toBe(200);
    expect(await store.verifySession(env.DB, other.rawSessionToken)).toBeNull();
  });

  it("a stolen session that drains its own budget can't stop the owner revoking it", async () => {
    const f = await firm();
    const thief = await store.createSession(env.DB, f.firmId, f.memberId);
    const thiefCookie = `dr_firm_session=${thief.rawSessionToken}`;
    for (let i = 0; i <= RATE_LIMIT_FIRM_SESSION_REVOKE.max; i++) {
      await send("DELETE", `/firm/sessions/${BOGUS}`, thiefCookie, null, null);
    }
    const thiefSession = await store.verifySession(env.DB, thief.rawSessionToken);
    expect((await send("DELETE", `/firm/sessions/${thiefSession!.sessionId}`, f.cookie, null, null)).status).toBe(200);
    expect(await store.verifySession(env.DB, thief.rawSessionToken)).toBeNull();
  });
});
