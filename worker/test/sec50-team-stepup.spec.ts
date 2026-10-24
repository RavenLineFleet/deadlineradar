/**
 * SEC-50 (HIGH, 2026-10-24): the four team-management handlers (invite,
 * role change, remove, make-primary) ran on a session cookie alone, so a
 * stolen Partner session up to SESSION_TTL_DAYS old could invite its own
 * Partner, make it primary (repointing firms.admin_email) and remove every
 * other member including the real owner. Each now requires the current
 * password, or for a passwordless member a sign-in within
 * store.RECENT_AUTH_WINDOW_MINUTES; make-primary also refuses a Partner who
 * has never signed in.
 *
 * Every refusal arm also asserts the state it guards did NOT change.
 */
import { env, SELF } from "cloudflare:test";
import { describe, expect, it } from "vitest";
import * as store from "../src/store";
import { hashPassword } from "../src/password";

const BASE = "https://deadline-radar.com";
const PASSWORD = "the-real-password";
const STALE = store.RECENT_AUTH_WINDOW_MINUTES + 1;

let seq = 0;
function uniqueEmail(label: string): string {
  seq += 1;
  return `sec50-${label}-${Date.now()}-${seq}@examplefirm.com`;
}

async function ownerFirm(opts: { password?: boolean; resetAuthorized?: boolean } = {}) {
  const email = uniqueEmail("owner");
  const firm = await store.createFirm(env.DB, { name: "SEC-50 LLP", adminEmail: email });
  await env.DB.prepare("UPDATE firms SET plan_tier = 'firm_starter' WHERE id = ?1").bind(firm.id).run();
  if (opts.password) {
    await store.setFirmMemberPassword(env.DB, firm.memberId, await hashPassword(PASSWORD));
  }
  const { rawSessionToken } = await store.createSession(env.DB, firm.id, firm.memberId, opts.resetAuthorized ?? false);
  const verified = await store.verifySession(env.DB, rawSessionToken);
  if (!verified) throw new Error("fresh session does not verify");
  return { firmId: firm.id, memberId: firm.memberId, email, sessionId: verified.sessionId, cookie: `dr_firm_session=${rawSessionToken}` };
}

async function addMember(firmId: string, role: store.FirmMemberRole, joined = true) {
  const email = uniqueEmail(role);
  const created = await store.createFirmMember(env.DB, { firmId, email, role });
  if (joined) await store.markFirmMemberJoined(env.DB, created.id);
  return { memberId: created.id, email };
}

async function ageSession(sessionId: string, minutes: number): Promise<void> {
  const createdAt = new Date(Date.now() - minutes * 60_000).toISOString();
  await env.DB.prepare("UPDATE firm_sessions SET created_at = ?1 WHERE id = ?2").bind(createdAt, sessionId).run();
}

function call(method: string, path: string, cookie: string, body?: Record<string, unknown>): Promise<Response> {
  return SELF.fetch(`${BASE}${path}`, {
    method,
    headers: { "content-type": "application/json", Cookie: cookie, Origin: BASE },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

const invite = (cookie: string, email: string, extra: Record<string, unknown> = {}) =>
  call("POST", "/firm/members/invite", cookie, { email, role: "partner", ...extra });
const roleChange = (cookie: string, id: string, extra: Record<string, unknown> = {}) =>
  call("PATCH", `/firm/members/${id}`, cookie, { role: "staff", ...extra });
const remove = (cookie: string, id: string, body?: Record<string, unknown>) => call("DELETE", `/firm/members/${id}`, cookie, body);
const makePrimary = (cookie: string, id: string, body?: Record<string, unknown>) =>
  call("POST", `/firm/members/${id}/make-primary`, cookie, body);

describe("SEC-50: a stale passwordless Partner session is refused at all four team handlers", () => {
  it("invite: 403 reauth_required, no member row created", async () => {
    const o = await ownerFirm();
    await ageSession(o.sessionId, STALE);
    const email = uniqueEmail("invitee");
    const resp = await invite(o.cookie, email);
    expect(resp.status).toBe(403);
    expect((await resp.json<{ reauth_required?: boolean }>()).reauth_required).toBe(true);
    expect(await store.findFirmMemberByEmail(env.DB, email)).toBeNull();
  });

  it("role change: 403, role unchanged", async () => {
    const o = await ownerFirm();
    const p2 = await addMember(o.firmId, "partner");
    await ageSession(o.sessionId, STALE);
    expect((await roleChange(o.cookie, p2.memberId)).status).toBe(403);
    expect((await store.getFirmMemberById(env.DB, o.firmId, p2.memberId))?.role).toBe("partner");
  });

  it("remove: 403, member still active", async () => {
    const o = await ownerFirm();
    const p2 = await addMember(o.firmId, "partner");
    await ageSession(o.sessionId, STALE);
    expect((await remove(o.cookie, p2.memberId)).status).toBe(403);
    expect(await store.getFirmMemberById(env.DB, o.firmId, p2.memberId)).not.toBeNull();
  });

  it("make-primary: 403, primary and admin_email unchanged", async () => {
    const o = await ownerFirm();
    const p2 = await addMember(o.firmId, "partner");
    await ageSession(o.sessionId, STALE);
    expect((await makePrimary(o.cookie, p2.memberId)).status).toBe(403);
    const firm = await store.getFirmById(env.DB, o.firmId);
    expect(firm?.primary_member_id).toBe(o.memberId);
    expect(firm?.admin_email).toBe(o.email);
  });

  it("positive control: the same handlers succeed inside the window", async () => {
    const o = await ownerFirm();
    const p2 = await addMember(o.firmId, "partner");
    const s3 = await addMember(o.firmId, "staff");
    await ageSession(o.sessionId, store.RECENT_AUTH_WINDOW_MINUTES - 1);
    expect((await invite(o.cookie, uniqueEmail("invitee"))).status).toBe(201);
    expect((await makePrimary(o.cookie, p2.memberId)).status).toBe(200);
    expect((await remove(o.cookie, s3.memberId)).status).toBe(200);
  });
});

describe("SEC-50: a password-holding Partner must supply the current password", () => {
  it("missing or wrong password is refused at all four handlers, with no state change", async () => {
    const o = await ownerFirm({ password: true });
    const p2 = await addMember(o.firmId, "partner");
    const email = uniqueEmail("invitee");
    for (const extra of [{}, { current_password: "wrong" }]) {
      expect((await invite(o.cookie, email, extra)).status).toBe(400);
      expect((await roleChange(o.cookie, p2.memberId, extra)).status).toBe(400);
      expect((await remove(o.cookie, p2.memberId, extra)).status).toBe(400);
      expect((await makePrimary(o.cookie, p2.memberId, extra)).status).toBe(400);
    }
    // No body at all on the two body-less verbs reads as no password.
    expect((await remove(o.cookie, p2.memberId)).status).toBe(400);
    expect((await makePrimary(o.cookie, p2.memberId)).status).toBe(400);

    expect(await store.findFirmMemberByEmail(env.DB, email)).toBeNull();
    const row = await store.getFirmMemberById(env.DB, o.firmId, p2.memberId);
    expect(row?.role).toBe("partner");
    expect((await store.getFirmById(env.DB, o.firmId))?.primary_member_id).toBe(o.memberId);
  });

  it("the correct password is accepted even on a stale session", async () => {
    const o = await ownerFirm({ password: true });
    const p2 = await addMember(o.firmId, "partner");
    const s3 = await addMember(o.firmId, "staff");
    await ageSession(o.sessionId, STALE);
    const ok = { current_password: PASSWORD };
    expect((await invite(o.cookie, uniqueEmail("invitee"), ok)).status).toBe(201);
    expect((await call("PATCH", `/firm/members/${s3.memberId}`, o.cookie, { role: "office_manager", ...ok })).status).toBe(200);
    expect((await makePrimary(o.cookie, p2.memberId, ok)).status).toBe(200);
    expect((await remove(o.cookie, s3.memberId, ok)).status).toBe(200);
  });

  it("a password-reset session's authority is not accepted in place of the password, and is not spent", async () => {
    const o = await ownerFirm({ password: true, resetAuthorized: true });
    const p2 = await addMember(o.firmId, "partner");
    expect((await makePrimary(o.cookie, p2.memberId)).status).toBe(400);
    const row = await env.DB.prepare("SELECT password_reset_authorized FROM firm_sessions WHERE id = ?1")
      .bind(o.sessionId)
      .first<{ password_reset_authorized: number }>();
    expect(row?.password_reset_authorized).toBe(1);
  });
});

describe("SEC-50: make-primary refuses a Partner who has never signed in", () => {
  it("400, admin_email unchanged, even with a fresh session", async () => {
    const o = await ownerFirm();
    const invited = await addMember(o.firmId, "partner", false);
    const resp = await makePrimary(o.cookie, invited.memberId);
    expect(resp.status).toBe(400);
    const firm = await store.getFirmById(env.DB, o.firmId);
    expect(firm?.primary_member_id).toBe(o.memberId);
    expect(firm?.admin_email).toBe(o.email);
  });
});

describe("SEC-50: the reported chain dies at leg 1 on a stale stolen session", () => {
  it("invite -> make-primary -> remove-owner: no step succeeds, the owner keeps the firm", async () => {
    const o = await ownerFirm();
    await ageSession(o.sessionId, STALE);
    const attackerEmail = uniqueEmail("attacker");
    expect((await invite(o.cookie, attackerEmail)).status).toBe(403);
    expect(await store.findFirmMemberByEmail(env.DB, attackerEmail)).toBeNull();

    // Even with a planted, joined attacker Partner, legs 3-4 still refuse.
    const planted = await addMember(o.firmId, "partner");
    expect((await makePrimary(o.cookie, planted.memberId)).status).toBe(403);
    expect((await remove(o.cookie, planted.memberId)).status).toBe(403);
    const firm = await store.getFirmById(env.DB, o.firmId);
    expect(firm?.primary_member_id).toBe(o.memberId);
    expect(firm?.admin_email).toBe(o.email);
  });
});
