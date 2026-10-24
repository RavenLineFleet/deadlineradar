import { env, SELF } from "cloudflare:test";
import { describe, expect, it } from "vitest";
import * as store from "../src/store";
import { hashPassword } from "../src/password";

/**
 * SecurityLab SEC-47 (MEDIUM, 2026-10-24; AuditLab CONFIRMED 9/9 arms).
 *
 * A session minted by redeeming a password-RESET link is exempt from proving
 * the current password at four gates: password set, account delete, 2FA
 * enroll, email change. The exemption was spent at the first only, and never
 * expired, so an abandoned reset left the other three password-free for the
 * session's full 30-day TTL with no limit on uses.
 *
 * Fixed on both axes, because either alone is a half fix: the flag is
 * honoured only within PASSWORD_RESET_AUTHORITY_TTL_MINUTES of session
 * creation (time), and every gate that honours it now spends it (count).
 * These arms discriminate on the authority's lifecycle, not on one status
 * code -- each runs against a member who HAS a password, so a 400 means the
 * gate demanded it.
 */

const BASE = "https://deadline-radar.com";
const PASSWORD = "the forgotten passphrase";
const KEY = randomKeyBase64();

function randomKeyBase64(): string {
  const bytes = new Uint8Array(32);
  crypto.getRandomValues(bytes);
  let bin = "";
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin);
}

function testExecutionContext(): ExecutionContext {
  return { waitUntil() {}, passThroughOnException() {}, props: {} } as unknown as ExecutionContext;
}

/** handleFirm2faEnroll 503s unless TOTP_ENCRYPTION_KEY is bound, which SELF can't override. */
async function workerFetch(request: Request, envOverrides: Record<string, unknown> = {}): Promise<Response> {
  const worker = (await import("../src/index")).default;
  return worker.fetch(request, { ...env, ...envOverrides } as never, testExecutionContext());
}

let seq = 0;
function uniq(label: string): string {
  seq += 1;
  return `${label}-${Date.now()}-${seq}`;
}

/** A firm whose primary member has a password, plus a session for that member. */
async function firmWithSession(label: string, resetAuthorized: boolean) {
  const { id: firmId } = await store.createFirm(env.DB, { name: "SEC-47 LLP", adminEmail: `${uniq(label)}@examplefirm.com` });
  const firm = await store.getFirmById(env.DB, firmId);
  const memberId = firm!.primary_member_id as string;
  await store.setFirmMemberPassword(env.DB, memberId, await hashPassword(PASSWORD));
  const { rawSessionToken } = await store.createSession(env.DB, firmId, memberId, resetAuthorized);
  return { firmId, memberId, cookie: `dr_firm_session=${rawSessionToken}` };
}

async function ageSession(memberId: string, minutes: number): Promise<void> {
  const createdAt = new Date(Date.now() - minutes * 60_000).toISOString();
  await env.DB.prepare(`UPDATE firm_sessions SET created_at = ?1 WHERE member_id = ?2`).bind(createdAt, memberId).run();
}

function changeEmail(cookie: string, newEmail: string): Promise<Response> {
  return SELF.fetch(`${BASE}/firm/change-email`, {
    method: "POST",
    headers: { "content-type": "application/json", "cf-connecting-ip": "203.0.113.47", Cookie: cookie },
    body: JSON.stringify({ new_email: newEmail }),
  });
}

function setPassword(cookie: string): Promise<Response> {
  return SELF.fetch(`${BASE}/firm/password`, {
    method: "POST",
    headers: { "content-type": "application/json", "cf-connecting-ip": "203.0.113.47", Cookie: cookie },
    body: JSON.stringify({ new_password: "a brand new passphrase" }),
  });
}

function deleteAccount(cookie: string): Promise<Response> {
  return SELF.fetch(`${BASE}/firm/account/delete`, {
    method: "POST",
    headers: { "content-type": "application/json", "cf-connecting-ip": "203.0.113.47", Cookie: cookie },
    body: JSON.stringify({}),
  });
}

function enroll2fa(cookie: string): Promise<Response> {
  return workerFetch(
    new Request(`${BASE}/firm/2fa/enroll`, {
      method: "POST",
      headers: { "content-type": "application/json", "cf-connecting-ip": "203.0.113.47", Cookie: cookie },
      body: JSON.stringify({}),
    }),
    { TOTP_ENCRYPTION_KEY: KEY }
  );
}

describe("SEC-47: the reset authority is bounded in count", () => {
  it("change-email honours it once, then demands the password", async () => {
    const { cookie } = await firmWithSession("sec47-count", true);
    expect((await changeEmail(cookie, `${uniq("sec47-count-a")}@example.com`)).status).toBe(200);
    expect((await changeEmail(cookie, `${uniq("sec47-count-b")}@example.com`)).status).toBe(400);
  });

  it("2FA enroll spends it -- a following change-email demands the password", async () => {
    const { cookie } = await firmWithSession("sec47-enroll", true);
    expect((await enroll2fa(cookie)).status).toBe(200);
    expect((await enroll2fa(cookie)).status).toBe(400);
    expect((await changeEmail(cookie, `${uniq("sec47-enroll-b")}@example.com`)).status).toBe(400);
  });

  it("change-email spends it -- a following account delete demands the password and deletes nothing", async () => {
    const { firmId, cookie } = await firmWithSession("sec47-delete", true);
    expect((await changeEmail(cookie, `${uniq("sec47-delete-a")}@example.com`)).status).toBe(200);
    expect((await deleteAccount(cookie)).status).toBe(400);
    expect((await store.getFirmById(env.DB, firmId))?.status).not.toBe(store.FIRM_STATUS_DELETED);
  });

  it("change-email spends it -- a following password set demands the old password", async () => {
    const { cookie } = await firmWithSession("sec47-cross-pw", true);
    expect((await changeEmail(cookie, `${uniq("sec47-cross-pw-a")}@example.com`)).status).toBe(200);
    expect((await setPassword(cookie)).status).toBe(400);
  });

  it("the conflicting-address no-op spends it too -- same generic success, same cost", async () => {
    const taken = `${uniq("sec47-taken")}@examplefirm.com`;
    await store.createFirm(env.DB, { name: "SEC-47 Taken LLP", adminEmail: taken });
    const { cookie } = await firmWithSession("sec47-conflict", true);
    expect((await changeEmail(cookie, taken)).status).toBe(200);
    expect((await changeEmail(cookie, `${uniq("sec47-conflict-b")}@example.com`)).status).toBe(400);
  });

  it("account delete honours it once (positive control: the exemption still works inside the window)", async () => {
    const { firmId, cookie } = await firmWithSession("sec47-delete-ok", true);
    expect((await deleteAccount(cookie)).status).toBe(200);
    expect((await store.getFirmById(env.DB, firmId))?.status).toBe(store.FIRM_STATUS_DELETED);
  });
});

describe("SEC-47: the reset authority is bounded in time", () => {
  const stale = store.PASSWORD_RESET_AUTHORITY_TTL_MINUTES + 1;
  const fresh = store.PASSWORD_RESET_AUTHORITY_TTL_MINUTES - 1;

  it("change-email: honoured just inside the window", async () => {
    const { memberId, cookie } = await firmWithSession("sec47-time-in", true);
    await ageSession(memberId, fresh);
    expect((await changeEmail(cookie, `${uniq("sec47-time-in")}@example.com`)).status).toBe(200);
  });

  it("change-email: refused just past the window, on a first use", async () => {
    const { memberId, cookie } = await firmWithSession("sec47-time-out", true);
    await ageSession(memberId, stale);
    expect((await changeEmail(cookie, `${uniq("sec47-time-out")}@example.com`)).status).toBe(400);
  });

  it("change-email: refused on a 29-day-old reset session (AuditLab arm E)", async () => {
    const { memberId, cookie } = await firmWithSession("sec47-time-29d", true);
    await ageSession(memberId, 29 * 24 * 60);
    expect((await changeEmail(cookie, `${uniq("sec47-time-29d")}@example.com`)).status).toBe(400);
  });

  it("account delete: refused past the window, and nothing is deleted", async () => {
    const { firmId, memberId, cookie } = await firmWithSession("sec47-time-delete", true);
    await ageSession(memberId, stale);
    expect((await deleteAccount(cookie)).status).toBe(400);
    expect((await store.getFirmById(env.DB, firmId))?.status).not.toBe(store.FIRM_STATUS_DELETED);
  });

  it("2FA enroll: refused past the window", async () => {
    const { memberId, cookie } = await firmWithSession("sec47-time-enroll", true);
    await ageSession(memberId, stale);
    expect((await enroll2fa(cookie)).status).toBe(400);
  });

  it("password set: refused past the window too -- a password set is itself a full takeover", async () => {
    const { memberId, cookie } = await firmWithSession("sec47-time-pw", true);
    await ageSession(memberId, stale);
    expect((await setPassword(cookie)).status).toBe(400);
  });

  it("an unparseable created_at reads as NOT authorized (fail closed)", async () => {
    const { memberId, cookie } = await firmWithSession("sec47-time-nan", true);
    await env.DB.prepare(`UPDATE firm_sessions SET created_at = 'not a date' WHERE member_id = ?1`).bind(memberId).run();
    expect((await changeEmail(cookie, `${uniq("sec47-time-nan")}@example.com`)).status).toBe(400);
  });
});

describe("SEC-47: controls", () => {
  it("a NORMAL session never had the exemption -- change-email demands the password", async () => {
    const { cookie } = await firmWithSession("sec47-normal", false);
    expect((await changeEmail(cookie, `${uniq("sec47-normal")}@example.com`)).status).toBe(400);
  });

  it("an aged reset session is still a valid session -- only the exemption lapsed", async () => {
    const { memberId, cookie } = await firmWithSession("sec47-alive", true);
    await ageSession(memberId, 29 * 24 * 60);
    const resp = await SELF.fetch(`${BASE}/firm/licenses`, { headers: { Cookie: cookie } });
    expect(resp.status).toBe(200);
  });
});
