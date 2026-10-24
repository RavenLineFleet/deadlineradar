/**
 * SEC-48 (HIGH, 2026-10-24): the four credential-changing step-up gates
 * (account delete, password set, 2FA enroll, email change) used to skip
 * entirely for a member with no password (magic-link / SSO only), so a
 * stolen session alone could lock the real owner out. They now require a
 * recent sign-in instead (store.RECENT_AUTH_WINDOW_MINUTES).
 *
 * Every arm ages a REAL session row's created_at rather than mocking the
 * clock, so the test exercises verifySession()'s own arithmetic.
 */
import { env } from "cloudflare:test";
import { describe, expect, it } from "vitest";
import * as store from "../src/store";

const BASE = "https://deadline-radar.com";
const KEY = randomKeyBase64();

function randomKeyBase64(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(32));
  let bin = "";
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin);
}

function testExecutionContext(): ExecutionContext {
  return { waitUntil() {}, passThroughOnException() {}, props: {} } as unknown as ExecutionContext;
}

async function workerFetch(request: Request): Promise<Response> {
  const worker = (await import("../src/index")).default;
  return worker.fetch(request, { ...env, TOTP_ENCRYPTION_KEY: KEY } as never, testExecutionContext());
}

let seq = 0;
async function passwordlessMember(label: string) {
  seq += 1;
  const email = `sec48-${label}-${Date.now()}-${seq}@examplefirm.com`;
  const { id: firmId, memberId } = await store.createFirm(env.DB, { name: "SEC-48 LLP", adminEmail: email });
  const { rawSessionToken } = await store.createSession(env.DB, firmId, memberId);
  const verified = await store.verifySession(env.DB, rawSessionToken);
  if (!verified) throw new Error("fresh session does not verify");
  return { firmId, memberId, email, sessionId: verified.sessionId, rawSessionToken, cookie: `dr_firm_session=${rawSessionToken}` };
}

async function ageSession(sessionId: string, minutes: number): Promise<void> {
  const createdAt = new Date(Date.now() - minutes * 60_000).toISOString();
  await env.DB.prepare("UPDATE firm_sessions SET created_at = ?1 WHERE id = ?2").bind(createdAt, sessionId).run();
}

function post(path: string, cookie: string, body: Record<string, unknown>, ip: string): Request {
  return new Request(`${BASE}${path}`, {
    method: "POST",
    headers: { "content-type": "application/json", "cf-connecting-ip": ip, Cookie: cookie, Origin: BASE },
    body: JSON.stringify(body),
  });
}

const STALE = store.RECENT_AUTH_WINDOW_MINUTES + 1;

describe("verifySession().recentlyAuthenticated", () => {
  it("is true for a fresh session, false past the window, false for an unparseable created_at", async () => {
    const m = await passwordlessMember("verify");
    expect((await store.verifySession(env.DB, m.rawSessionToken))?.recentlyAuthenticated).toBe(true);
    await ageSession(m.sessionId, store.RECENT_AUTH_WINDOW_MINUTES - 1);
    expect((await store.verifySession(env.DB, m.rawSessionToken))?.recentlyAuthenticated).toBe(true);
    await ageSession(m.sessionId, STALE);
    expect((await store.verifySession(env.DB, m.rawSessionToken))?.recentlyAuthenticated).toBe(false);
    await env.DB.prepare("UPDATE firm_sessions SET created_at = 'not-a-date' WHERE id = ?1").bind(m.sessionId).run();
    expect((await store.verifySession(env.DB, m.rawSessionToken))?.recentlyAuthenticated).toBe(false);
  });

  it("the window is its own 15-minute literal", () => {
    expect(store.RECENT_AUTH_WINDOW_MINUTES).toBe(15);
  });
});

describe("SEC-48: a stale session of a passwordless member is refused at all four gates", () => {
  it("POST /firm/2fa/enroll: 403, no secret issued", async () => {
    const m = await passwordlessMember("enroll");
    await ageSession(m.sessionId, STALE);
    const resp = await workerFetch(post("/firm/2fa/enroll", m.cookie, {}, "203.0.113.180"));
    expect(resp.status).toBe(403);
    const json = (await resp.json()) as Record<string, unknown>;
    expect(json.reauth_required).toBe(true);
    expect(json.secret).toBeUndefined();
    expect(json.enrollment_token).toBeUndefined();
  });

  it("POST /firm/password: 403, no password written", async () => {
    const m = await passwordlessMember("pwset");
    await ageSession(m.sessionId, STALE);
    const resp = await workerFetch(post("/firm/password", m.cookie, { new_password: "Sec48-Durable-Credential-91!" }, "203.0.113.181"));
    expect(resp.status).toBe(403);
    expect(((await resp.json()) as Record<string, unknown>).reauth_required).toBe(true);
    const member = await store.getFirmMemberById(env.DB, m.firmId, m.memberId);
    expect(member?.password_hash ?? null).toBeNull();
  });

  it("POST /firm/change-email: 403, no change token minted", async () => {
    const m = await passwordlessMember("chgemail");
    await ageSession(m.sessionId, STALE);
    const resp = await workerFetch(post("/firm/change-email", m.cookie, { new_email: `sec48-attacker-${Date.now()}@example.com` }, "203.0.113.182"));
    expect(resp.status).toBe(403);
    expect(((await resp.json()) as Record<string, unknown>).reauth_required).toBe(true);
    const row = await env.DB.prepare("SELECT COUNT(*) AS n FROM firm_login_tokens WHERE firm_id = ?1 AND purpose = 'email_change'")
      .bind(m.firmId)
      .first<{ n: number }>();
    expect(row?.n).toBe(0);
  });

  it("POST /firm/account/delete: 403, firm stays active", async () => {
    const m = await passwordlessMember("delete");
    await ageSession(m.sessionId, STALE);
    const resp = await workerFetch(post("/firm/account/delete", m.cookie, { reason: "other" }, "203.0.113.183"));
    expect(resp.status).toBe(403);
    expect(((await resp.json()) as Record<string, unknown>).reauth_required).toBe(true);
    const firm = await store.getFirmById(env.DB, m.firmId);
    expect(firm?.status).toBe("active");
  });
});

describe("SEC-48 positive controls: a fresh session of the same member still passes each gate", () => {
  it("POST /firm/2fa/enroll: 200", async () => {
    const m = await passwordlessMember("enroll-ok");
    const resp = await workerFetch(post("/firm/2fa/enroll", m.cookie, {}, "203.0.113.184"));
    expect(resp.status).toBe(200);
  });

  it("POST /firm/password: 200, password written", async () => {
    const m = await passwordlessMember("pwset-ok");
    const resp = await workerFetch(post("/firm/password", m.cookie, { new_password: "Sec48-Durable-Credential-91!" }, "203.0.113.185"));
    expect(resp.status).toBe(200);
    const member = await store.getFirmMemberById(env.DB, m.firmId, m.memberId);
    expect(member?.password_hash).toBeTruthy();
  });

  it("POST /firm/change-email: not refused by the step-up", async () => {
    const m = await passwordlessMember("chgemail-ok");
    const resp = await workerFetch(post("/firm/change-email", m.cookie, { new_email: `sec48-new-${Date.now()}@example.com` }, "203.0.113.186"));
    expect(resp.status).toBe(200);
  });

  it("POST /firm/account/delete: 200", async () => {
    const m = await passwordlessMember("delete-ok");
    const resp = await workerFetch(post("/firm/account/delete", m.cookie, { reason: "other" }, "203.0.113.187"));
    expect(resp.status).toBe(200);
  });
});

describe("SEC-48 does not weaken the password path", () => {
  it("a member WITH a password and a fresh session still needs the password", async () => {
    const m = await passwordlessMember("haspw");
    const { hashPassword } = await import("../src/password");
    await store.setFirmMemberPassword(env.DB, m.memberId, await hashPassword("Sec48-Real-Owner-Pass-77!"));
    const resp = await workerFetch(post("/firm/2fa/enroll", m.cookie, {}, "203.0.113.188"));
    expect(resp.status).toBe(400);
  });
});
