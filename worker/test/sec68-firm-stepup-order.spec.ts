/**
 * SEC-68 (LOW, 2026-11-06): the SEC-67 ordering defect on the firm routes. A
 * step-up refusal for a passwordless member on a stale session spent a slot in
 * a bucket that signing back in doesn't clear, so following the 403's advice
 * could earn a 429. At /2fa/enroll/confirm the shared bucket also gave a stolen
 * stale session a one-hour veto on the owner's enrollment.
 *
 * Each arm counts rows in rate_limit_hits as well as reading the status, since
 * status alone can't tell "not metered" from "metered and under the cap".
 */
import { env } from "cloudflare:test";
import { describe, expect, it } from "vitest";
import * as store from "../src/store";
import { hashPassword } from "../src/password";
import { generateTotp } from "../src/totp";
import { RATE_LIMIT_FIRM_2FA_ENROLL } from "../src/validation";

const BASE = "https://deadline-radar.com";
const KEY = randomKeyBase64();
const PASSWORD = "correct horse battery staple 68";
const STALE = store.RECENT_AUTH_WINDOW_MINUTES + 1;

function randomKeyBase64(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(32));
  let bin = "";
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin);
}

async function workerFetch(request: Request): Promise<Response> {
  const worker = (await import("../src/index")).default;
  const ctx = { waitUntil() {}, passThroughOnException() {}, props: {} } as unknown as ExecutionContext;
  return worker.fetch(request, { ...env, TOTP_ENCRYPTION_KEY: KEY } as never, ctx);
}

let seq = 0;
function uniqueEmail(label: string): string {
  seq += 1;
  return `sec68-${label}-${Date.now()}-${seq}@examplefirm.com`;
}
function uniqueIp(): string {
  seq += 1;
  return `198.51.${Math.floor(seq / 250) + 100}.${(seq % 250) + 1}`;
}

async function firm(opts: { password?: boolean; resetAuthorized?: boolean } = {}) {
  const f = await store.createFirm(env.DB, { name: "SEC-68 LLP", adminEmail: uniqueEmail("owner") });
  await env.DB.prepare("UPDATE firms SET plan_tier = 'firm_starter' WHERE id = ?1").bind(f.id).run();
  if (opts.password) await store.setFirmMemberPassword(env.DB, f.memberId, await hashPassword(PASSWORD));
  const { rawSessionToken } = await store.createSession(env.DB, f.id, f.memberId, opts.resetAuthorized ?? false);
  const verified = await store.verifySession(env.DB, rawSessionToken);
  if (!verified) throw new Error("fresh session does not verify");
  return { firmId: f.id, memberId: f.memberId, sessionId: verified.sessionId, cookie: `dr_firm_session=${rawSessionToken}` };
}
type Firm = Awaited<ReturnType<typeof firm>>;

async function age(sessionId: string): Promise<void> {
  const createdAt = new Date(Date.now() - STALE * 60_000).toISOString();
  await env.DB.prepare("UPDATE firm_sessions SET created_at = ?1 WHERE id = ?2").bind(createdAt, sessionId).run();
}

async function freshSession(f: Firm): Promise<string> {
  const { rawSessionToken } = await store.createSession(env.DB, f.firmId, f.memberId);
  return `dr_firm_session=${rawSessionToken}`;
}

async function teammate(firmId: string): Promise<string> {
  const created = await store.createFirmMember(env.DB, { firmId, email: uniqueEmail("partner"), role: "partner" });
  await store.markFirmMemberJoined(env.DB, created.id);
  return created.id;
}

function call(method: string, path: string, cookie: string, body: Record<string, unknown>, ip: string): Promise<Response> {
  return workerFetch(
    new Request(`${BASE}${path}`, {
      method,
      headers: { "content-type": "application/json", "cf-connecting-ip": ip, Cookie: cookie, Origin: BASE },
      body: JSON.stringify(body),
    })
  );
}

async function slots(key: string, bucket: string): Promise<number> {
  const row = await env.DB.prepare("SELECT COUNT(*) AS n FROM rate_limit_hits WHERE ip = ?1 AND bucket = ?2").bind(key, bucket).first<{ n: number }>();
  return row?.n ?? 0;
}

interface Site {
  name: string;
  bucket: string;
  max: number;
  keyedOnIp?: boolean;
  keyedOnMember?: boolean;
  request: (f: Firm, cookie: string, ip: string) => Promise<Response>;
}

const SITES: Site[] = [
  { name: "change email", bucket: "firm_change_email", max: 5, request: (_f, c, ip) => call("POST", "/firm/change-email", c, { new_email: uniqueEmail("new") }, ip) },
  { name: "account delete", bucket: "firm_account_delete", max: 3, request: (_f, c, ip) => call("POST", "/firm/account/delete", c, {}, ip) },
  { name: "password set", bucket: "firm_password_set", max: 10, keyedOnIp: true, request: (_f, c, ip) => call("POST", "/firm/password", c, { new_password: "a-brand-new-long-password-68" }, ip) },
  { name: "2FA enroll", bucket: "firm_2fa_enroll", max: 8, keyedOnMember: true, request: (_f, c, ip) => call("POST", "/firm/2fa/enroll", c, {}, ip) },
  { name: "member invite", bucket: "firm_member_invite", max: 30, request: (_f, c, ip) => call("POST", "/firm/members/invite", c, { email: uniqueEmail("invitee"), role: "staff" }, ip) },
  { name: "role change", bucket: "firm_member_role_change", max: 50, request: async (f, c, ip) => call("PATCH", `/firm/members/${await teammate(f.firmId)}`, c, { role: "staff" }, ip) },
  { name: "member remove", bucket: "firm_member_remove", max: 50, request: async (f, c, ip) => call("DELETE", `/firm/members/${await teammate(f.firmId)}`, c, {}, ip) },
  { name: "make primary", bucket: "firm_member_make_primary", max: 20, request: async (f, c, ip) => call("POST", `/firm/members/${await teammate(f.firmId)}/make-primary`, c, {}, ip) },
];

function keyFor(site: Site, f: Firm, ip: string): string {
  if (site.keyedOnIp) return ip;
  return site.keyedOnMember ? f.memberId : f.firmId;
}

describe("SEC-68: a stale passwordless refusal spends no slot", () => {
  for (const site of SITES) {
    it(`${site.name}: one past the cap is still 403, with nothing written to the bucket`, async () => {
      const f = await firm();
      await age(f.sessionId);
      const ip = uniqueIp();
      for (let i = 0; i <= site.max; i++) {
        const resp = await site.request(f, f.cookie, ip);
        expect(resp.status).toBe(403);
        expect(((await resp.json()) as { reauth_required?: boolean }).reauth_required).toBe(true);
      }
      expect(await slots(keyFor(site, f, ip), site.bucket)).toBe(0);
    });

    it(`${site.name}: a fresh passwordless session still meets the limiter`, async () => {
      const f = await firm();
      const ip = uniqueIp();
      const resp = await site.request(f, f.cookie, ip);
      expect(resp.status).not.toBe(403);
      expect(await slots(keyFor(site, f, ip), site.bucket)).toBe(1);
    });

    it(`${site.name}: a stale member with a password still spends a slot, because a wrong password is a guess`, async () => {
      const f = await firm({ password: true });
      await age(f.sessionId);
      const ip = uniqueIp();
      const resp = await site.request(f, f.cookie, ip);
      expect(resp.status).toBe(400);
      expect(await slots(keyFor(site, f, ip), site.bucket)).toBe(1);
    });

    it(`${site.name}: a reset session past its window gets the same 403, because its authority aged out too`, async () => {
      const f = await firm({ resetAuthorized: true });
      await age(f.sessionId);
      const ip = uniqueIp();
      const resp = await site.request(f, f.cookie, ip);
      expect(resp.status).toBe(403);
      expect(await slots(keyFor(site, f, ip), site.bucket)).toBe(0);
    });
  }

  it("the password arm's own cap still holds: account delete 429s on the 4th wrong password", async () => {
    const f = await firm({ password: true });
    await age(f.sessionId);
    const statuses: number[] = [];
    for (let i = 0; i < 4; i++) {
      statuses.push((await call("POST", "/firm/account/delete", f.cookie, { current_password: "wrong" }, uniqueIp())).status);
    }
    expect(statuses).toEqual([400, 400, 400, 429]);
  });

  it("a stale demo session still gets the demo refusal, not advice to sign in again", async () => {
    const f = await firm();
    await env.DB.prepare("UPDATE firms SET demo_locked = 1 WHERE id = ?1").bind(f.firmId).run();
    await age(f.sessionId);
    const resp = await call("POST", "/firm/change-email", f.cookie, { new_email: uniqueEmail("new") }, uniqueIp());
    expect(resp.status).toBe(403);
    const json = (await resp.json()) as { error: string; reauth_required?: boolean };
    expect(json.reauth_required).toBeUndefined();
    expect(json.error).toContain("shared demo account");
  });
});

describe("SEC-68: a stolen stale session has no veto on 2FA enrollment", () => {
  async function enroll(cookie: string): Promise<{ secret: string; token: string }> {
    const resp = await call("POST", "/firm/2fa/enroll", cookie, {}, uniqueIp());
    expect(resp.status).toBe(200);
    const json = (await resp.json()) as { secret: string; enrollment_token: string };
    return { secret: json.secret, token: json.enrollment_token };
  }

  it("junk posts to enroll and confirm fill nothing, and the owner then finishes enrolling", async () => {
    const f = await firm();
    await age(f.sessionId);
    const max = RATE_LIMIT_FIRM_2FA_ENROLL.max;
    for (let i = 0; i <= max; i++) {
      expect((await call("POST", "/firm/2fa/enroll", f.cookie, {}, uniqueIp())).status).toBe(403);
      expect((await call("POST", "/firm/2fa/enroll/confirm", f.cookie, { enrollment_token: "junk", code: "000000" }, uniqueIp())).status).toBe(400);
      expect((await call("POST", "/firm/2fa/enroll/confirm", f.cookie, { code: "000000" }, uniqueIp())).status).toBe(400);
    }
    expect(await slots(f.memberId, "firm_2fa_enroll")).toBe(0);

    const owner = await freshSession(f);
    const { secret, token } = await enroll(owner);
    const done = await call("POST", "/firm/2fa/enroll/confirm", owner, { enrollment_token: token, code: await generateTotp(secret) }, uniqueIp());
    expect(done.status).toBe(200);
  });

  it("every request-shape refusal at confirm is free: SEC-69's seven paths", async () => {
    const f = await firm();
    const post = (contentType: string | null, body: string) => {
      const headers: Record<string, string> = { "cf-connecting-ip": uniqueIp(), Cookie: f.cookie, Origin: BASE };
      if (contentType) headers["content-type"] = contentType;
      return workerFetch(new Request(`${BASE}/firm/2fa/enroll/confirm`, { method: "POST", headers, body }));
    };
    const shapes: Array<[string | null, string]> = [
      ["text/plain", "x"], // not JSON
      ["application/json", ""], // empty
      ["application/json", "{not json"], // unparseable
      ["application/json", "7"], // scalar
      ["application/json", "[]"], // array
      ["application/json", "{}"], // no code
      ["application/json", JSON.stringify({ code: "000000" })], // no token: no crypto runs
    ];
    for (const [contentType, body] of shapes) {
      expect((await post(contentType, body)).status).toBe(400);
    }
    expect(await slots(f.memberId, "firm_2fa_enroll")).toBe(0);
  });

  it("a token sealed for another session spends no slot either", async () => {
    const f = await firm();
    const owner = await freshSession(f);
    const { token } = await enroll(owner);
    await age(f.sessionId);
    const before = await slots(f.memberId, "firm_2fa_enroll");
    const resp = await call("POST", "/firm/2fa/enroll/confirm", f.cookie, { enrollment_token: token, code: "000000" }, uniqueIp());
    expect(resp.status).toBe(400);
    expect(await slots(f.memberId, "firm_2fa_enroll")).toBe(before);
  });

  it("the code-guessing bound still holds for a caller with a real token", async () => {
    const f = await firm();
    const { token } = await enroll(f.cookie);
    const statuses: number[] = [];
    for (let i = 0; i < RATE_LIMIT_FIRM_2FA_ENROLL.max; i++) {
      statuses.push((await call("POST", "/firm/2fa/enroll/confirm", f.cookie, { enrollment_token: token, code: "not-a-code" }, uniqueIp())).status);
    }
    // enroll spent one slot, so the eighth attempt is the first refused.
    expect(statuses.slice(0, 7)).toEqual(Array(7).fill(400));
    expect(statuses[7]).toBe(429);
  });
});
