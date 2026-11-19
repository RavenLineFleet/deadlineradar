/**
 * SEC-107 (SecurityLab LOW, AuditLab CONFIRMED, 2026-11-19): POST /firm/password
 * bounded current-password guesses with an IP-keyed bucket only, so a stolen
 * session could test candidates against one member without limit by rotating
 * source addresses. It now also spends a bucket keyed on the session, and
 * spends both only after every refusal a caller can trigger for free.
 *
 * SEC-108 (SecurityLab LOW, AuditLab CONFIRMED, 2026-11-19): that bucket was
 * first keyed on session.memberId. The route evicts the member's other
 * sessions, so a stolen session could hold the member key at cap and lock the
 * owner out of the very control meant to evict it. It is now keyed on
 * session.sessionId, the SEC-71 rule for eviction controls.
 *
 * Each arm counts rows in rate_limit_hits as well as reading the status.
 */
import { env } from "cloudflare:test";
import { describe, expect, it } from "vitest";
import * as store from "../src/store";
import { hashPassword } from "../src/password";
import { RATE_LIMIT_FIRM_PASSWORD_SET_SESSION } from "../src/validation";

const BASE = "https://deadline-radar.com";
const PASSWORD = "correct horse battery staple 107";
const WRONG = "not the right password 107";
const NEW_PASSWORD = "a-brand-new-long-password-107";

async function workerFetch(request: Request): Promise<Response> {
  const worker = (await import("../src/index")).default;
  const ctx = { waitUntil() {}, passThroughOnException() {}, props: {} } as unknown as ExecutionContext;
  return worker.fetch(request, env as never, ctx);
}

let seq = 0;
function uniqueEmail(label: string): string {
  seq += 1;
  return `sec107-${label}-${Date.now()}-${seq}@examplefirm.com`;
}
function uniqueIp(): string {
  seq += 1;
  return `198.18.${Math.floor(seq / 250) + 10}.${(seq % 250) + 1}`;
}

async function sessionFor(firmId: string, memberId: string, resetAuthorized = false): Promise<{ sessionId: string; cookie: string }> {
  const { rawSessionToken } = await store.createSession(env.DB, firmId, memberId, resetAuthorized);
  const verified = await store.verifySession(env.DB, rawSessionToken);
  if (!verified) throw new Error("fresh session does not verify");
  return { sessionId: verified.sessionId, cookie: `dr_firm_session=${rawSessionToken}` };
}

async function firmWithPassword() {
  const f = await store.createFirm(env.DB, { name: "SEC-107 LLP", adminEmail: uniqueEmail("owner") });
  await env.DB.prepare("UPDATE firms SET plan_tier = 'firm_starter' WHERE id = ?1").bind(f.id).run();
  await store.setFirmMemberPassword(env.DB, f.memberId, await hashPassword(PASSWORD));
  const s = await sessionFor(f.id, f.memberId);
  return { firmId: f.id, memberId: f.memberId, sessionId: s.sessionId, cookie: s.cookie };
}

async function joinedTeammateWithSession(firmId: string): Promise<{ memberId: string; sessionId: string; cookie: string }> {
  const created = await store.createFirmMember(env.DB, { firmId, email: uniqueEmail("partner"), role: "partner" });
  await store.markFirmMemberJoined(env.DB, created.id);
  await store.setFirmMemberPassword(env.DB, created.id, await hashPassword(PASSWORD));
  const s = await sessionFor(firmId, created.id);
  return { memberId: created.id, sessionId: s.sessionId, cookie: s.cookie };
}

async function liveSessionIds(memberId: string): Promise<string[]> {
  const rows = await env.DB.prepare("SELECT id FROM firm_sessions WHERE member_id = ?1").bind(memberId).all<{ id: string }>();
  return rows.results.map((r) => r.id);
}

const BUCKET = "firm_password_set_session";

function post(cookie: string, body: string, ip: string): Promise<Response> {
  return workerFetch(
    new Request(`${BASE}/firm/password`, {
      method: "POST",
      headers: { "content-type": "application/json", "cf-connecting-ip": ip, Cookie: cookie, Origin: BASE },
      body,
    })
  );
}

function guess(cookie: string, ip: string): Promise<Response> {
  return post(cookie, JSON.stringify({ new_password: NEW_PASSWORD, current_password: WRONG }), ip);
}

async function slots(key: string, bucket: string): Promise<number> {
  const row = await env.DB.prepare("SELECT COUNT(*) AS n FROM rate_limit_hits WHERE ip = ?1 AND bucket = ?2").bind(key, bucket).first<{ n: number }>();
  return row?.n ?? 0;
}

describe("SEC-107: the password-set guess budget is bounded per session, not only per IP", () => {
  it("rotating the source IP on every guess still meets a 429 one past the session cap", async () => {
    const f = await firmWithPassword();
    const max = RATE_LIMIT_FIRM_PASSWORD_SET_SESSION.max;
    for (let i = 0; i < max; i++) {
      const resp = await guess(f.cookie, uniqueIp());
      expect(resp.status).toBe(400);
      expect(((await resp.json()) as { error: string }).error).toBe("That current password isn't right.");
    }
    expect(await slots(f.sessionId, BUCKET)).toBe(max);
    const blocked = await guess(f.cookie, uniqueIp());
    expect(blocked.status).toBe(429);
    // The cap holds for the RIGHT password too, so the budget isn't a hint.
    const right = await post(f.cookie, JSON.stringify({ new_password: NEW_PASSWORD, current_password: PASSWORD }), uniqueIp());
    expect(right.status).toBe(429);
  });

  it("one member exhausting their budget doesn't block a teammate in the same firm", async () => {
    const f = await firmWithPassword();
    const mate = await joinedTeammateWithSession(f.firmId);
    for (let i = 0; i < RATE_LIMIT_FIRM_PASSWORD_SET_SESSION.max; i++) await guess(f.cookie, uniqueIp());
    expect((await guess(f.cookie, uniqueIp())).status).toBe(429);
    const resp = await post(mate.cookie, JSON.stringify({ new_password: NEW_PASSWORD, current_password: PASSWORD }), uniqueIp());
    expect(resp.status).toBe(200);
    expect(await slots(mate.sessionId, BUCKET)).toBe(1);
  });

  it("the IP bucket still applies alongside the session bucket", async () => {
    const f = await firmWithPassword();
    const ip = uniqueIp();
    await guess(f.cookie, ip);
    expect(await slots(ip, "firm_password_set")).toBe(1);
    expect(await slots(f.sessionId, BUCKET)).toBe(1);
  });

  it("a demo_locked 403 spends no slot in either bucket, even one past the cap", async () => {
    const f = await firmWithPassword();
    await env.DB.prepare("UPDATE firms SET demo_locked = 1 WHERE id = ?1").bind(f.firmId).run();
    const ip = uniqueIp();
    for (let i = 0; i <= RATE_LIMIT_FIRM_PASSWORD_SET_SESSION.max; i++) {
      expect((await guess(f.cookie, ip)).status).toBe(403);
    }
    expect(await slots(ip, "firm_password_set")).toBe(0);
    expect(await slots(f.sessionId, BUCKET)).toBe(0);
  });

  it("free refusals (malformed JSON, weak new password) spend no slot", async () => {
    const f = await firmWithPassword();
    const ip = uniqueIp();
    expect((await post(f.cookie, "{not json", ip)).status).toBe(400);
    expect((await post(f.cookie, JSON.stringify({ new_password: "short", current_password: WRONG }), ip)).status).toBe(400);
    expect(await slots(ip, "firm_password_set")).toBe(0);
    expect(await slots(f.sessionId, BUCKET)).toBe(0);
  });
});

describe("SEC-108: a stolen session cannot drain the owner's password-set budget", () => {
  async function drain(cookie: string): Promise<void> {
    for (let i = 0; i < RATE_LIMIT_FIRM_PASSWORD_SET_SESSION.max; i++) await guess(cookie, uniqueIp());
    expect((await guess(cookie, uniqueIp())).status).toBe(429);
  }

  it("a reset-link session sets the password and evicts the stolen session while the stolen one is at cap", async () => {
    const f = await firmWithPassword(); // f.cookie plays the stolen session
    await drain(f.cookie);
    const reset = await sessionFor(f.firmId, f.memberId, true);
    const resp = await post(reset.cookie, JSON.stringify({ new_password: NEW_PASSWORD }), uniqueIp());
    expect(resp.status).toBe(200);
    expect(await slots(reset.sessionId, BUCKET)).toBe(1);
    expect(await liveSessionIds(f.memberId)).toEqual([reset.sessionId]);
  });

  it("the owner's own other session changes the password with the current one while the stolen one is at cap", async () => {
    const f = await firmWithPassword();
    await drain(f.cookie);
    const owner = await sessionFor(f.firmId, f.memberId);
    const resp = await post(owner.cookie, JSON.stringify({ new_password: NEW_PASSWORD, current_password: PASSWORD }), uniqueIp());
    expect(resp.status).toBe(200);
    expect(await liveSessionIds(f.memberId)).toEqual([owner.sessionId]);
  });

  it("the drained bucket belongs to the stolen session only, never to the member", async () => {
    const f = await firmWithPassword();
    await drain(f.cookie);
    expect(await slots(f.sessionId, BUCKET)).toBe(RATE_LIMIT_FIRM_PASSWORD_SET_SESSION.max);
    expect(await slots(f.memberId, BUCKET)).toBe(0);
    expect(await slots(f.memberId, "firm_password_set_member")).toBe(0);
  });
});
