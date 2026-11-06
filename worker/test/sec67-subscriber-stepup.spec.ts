/**
 * SEC-67 (2026-11-06): /subscriber/change-email now needs a recent sign-in.
 * This tier has no password, so the step-up is the passwordless arm of the
 * firm gate (SEC-48): a session younger than RECENT_AUTH_WINDOW_MINUTES.
 */
import { env } from "cloudflare:test";
import { describe, expect, it, vi } from "vitest";
import * as store from "../src/store";

const BASE = "https://deadline-radar.com";
const STALE = store.RECENT_AUTH_WINDOW_MINUTES + 1;

async function subscriber(tag: string): Promise<{ email: string; cookie: string; rawSessionToken: string; sessionId: string }> {
  const email = `sec67-${tag}-${Date.now()}@example.com`;
  await store.addPending(env.DB, {
    email,
    stateSlug: "ohio",
    deadlineFields: {},
    firstName: null,
    skipConfirmation: true,
    firmId: null,
  });
  const { rawSessionToken, sessionId } = await store.createSubscriberSession(env.DB, store.normalizeEmail(email));
  return { email, cookie: `dr_sub_session=${rawSessionToken}`, rawSessionToken, sessionId };
}

async function ageSession(sessionId: string, minutes: number): Promise<void> {
  await env.DB.prepare("UPDATE subscriber_sessions SET created_at = ?1 WHERE id = ?2")
    .bind(new Date(Date.now() - minutes * 60_000).toISOString(), sessionId)
    .run();
}

async function post(path: string, cookie: string, body: unknown, ip: string): Promise<Response> {
  const worker = (await import("../src/index")).default;
  return worker.fetch(
    new Request(`${BASE}${path}`, {
      method: "POST",
      headers: { "content-type": "application/json", "cf-connecting-ip": ip, Cookie: cookie },
      body: JSON.stringify(body),
    }),
    { ...env, RESEND_API_KEY: "test-key-not-real" },
    { waitUntil: () => {}, passThroughOnException: () => {} } as unknown as ExecutionContext
  );
}

async function changeTokenCount(email: string): Promise<number> {
  const row = await env.DB.prepare(
    "SELECT COUNT(*) AS n FROM subscriber_login_tokens WHERE email_normalized = ?1 AND purpose = 'email_change'"
  )
    .bind(store.normalizeEmail(email))
    .first<{ n: number }>();
  return row?.n ?? -1;
}

describe("verifySubscriberSession().recentlyAuthenticated", () => {
  it("is true for a fresh session, false past the window, false for an unparseable created_at", async () => {
    const s = await subscriber("verify");
    expect((await store.verifySubscriberSession(env.DB, s.rawSessionToken))?.recentlyAuthenticated).toBe(true);
    await ageSession(s.sessionId, store.RECENT_AUTH_WINDOW_MINUTES - 1);
    expect((await store.verifySubscriberSession(env.DB, s.rawSessionToken))?.recentlyAuthenticated).toBe(true);
    await ageSession(s.sessionId, STALE);
    expect((await store.verifySubscriberSession(env.DB, s.rawSessionToken))?.recentlyAuthenticated).toBe(false);
    await env.DB.prepare("UPDATE subscriber_sessions SET created_at = 'not-a-date' WHERE id = ?1").bind(s.sessionId).run();
    expect((await store.verifySubscriberSession(env.DB, s.rawSessionToken))?.recentlyAuthenticated).toBe(false);
  });
});

describe("SEC-67: POST /subscriber/change-email step-up", () => {
  it("a stale session is refused: 403 reauth_required, no token minted, no email sent", async () => {
    const s = await subscriber("stale");
    await ageSession(s.sessionId, STALE);
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("{}", { status: 202 }));
    try {
      const resp = await post("/subscriber/change-email", s.cookie, { new_email: `sec67-attacker-${Date.now()}@example.com` }, "203.0.113.190");
      expect(resp.status).toBe(403);
      const json = (await resp.json()) as Record<string, unknown>;
      expect(json.reauth_required).toBe(true);
      expect(String(json.error)).toContain("sign out and sign back in");
      expect(fetchSpy).not.toHaveBeenCalled();
    } finally {
      fetchSpy.mockRestore();
    }
    expect(await changeTokenCount(s.email)).toBe(0);
  });

  it("an unparseable created_at is refused the same way", async () => {
    const s = await subscriber("nan");
    await env.DB.prepare("UPDATE subscriber_sessions SET created_at = 'not-a-date' WHERE id = ?1").bind(s.sessionId).run();
    const resp = await post("/subscriber/change-email", s.cookie, { new_email: `sec67-nan-${Date.now()}@example.com` }, "203.0.113.191");
    expect(resp.status).toBe(403);
    expect(await changeTokenCount(s.email)).toBe(0);
  });

  it("positive control: the same request on a fresh session goes through and mints the token", async () => {
    const s = await subscriber("fresh");
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("{}", { status: 202 }));
    try {
      const resp = await post("/subscriber/change-email", s.cookie, { new_email: `sec67-new-${Date.now()}@example.com` }, "203.0.113.192");
      expect(resp.status).toBe(200);
    } finally {
      fetchSpy.mockRestore();
    }
    expect(await changeTokenCount(s.email)).toBe(1);
  });

  it("a fresh sign-in clears the bar for someone whose first session went stale", async () => {
    const s = await subscriber("resign");
    await ageSession(s.sessionId, STALE);
    const target = `sec67-resign-new-${Date.now()}@example.com`;
    expect((await post("/subscriber/change-email", s.cookie, { new_email: target }, "203.0.113.193")).status).toBe(403);
    const again = await store.createSubscriberSession(env.DB, store.normalizeEmail(s.email));
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("{}", { status: 202 }));
    try {
      const resp = await post("/subscriber/change-email", `dr_sub_session=${again.rawSessionToken}`, { new_email: target }, "203.0.113.194");
      expect(resp.status).toBe(200);
    } finally {
      fetchSpy.mockRestore();
    }
    expect(await changeTokenCount(s.email)).toBe(1);
  });

  it("refusals don't spend the send throttle, so the 403's own remedy still works after six of them", async () => {
    const s = await subscriber("bucket");
    await ageSession(s.sessionId, STALE);
    const target = `sec67-bucket-new-${Date.now()}@example.com`;
    for (let i = 0; i < 6; i++) {
      expect((await post("/subscriber/change-email", s.cookie, { new_email: target }, "203.0.113.197")).status).toBe(403);
    }
    const again = await store.createSubscriberSession(env.DB, store.normalizeEmail(s.email));
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("{}", { status: 202 }));
    try {
      const resp = await post("/subscriber/change-email", `dr_sub_session=${again.rawSessionToken}`, { new_email: target }, "203.0.113.198");
      expect(resp.status).toBe(200);
    } finally {
      fetchSpy.mockRestore();
    }
    expect(await changeTokenCount(s.email)).toBe(1);
  });

  it("the send throttle still applies to a fresh session", async () => {
    const s = await subscriber("throttle");
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("{}", { status: 202 }));
    const statuses: number[] = [];
    try {
      for (let i = 0; i < 6; i++) {
        const resp = await post("/subscriber/change-email", s.cookie, { new_email: `sec67-throttle-${i}-${Date.now()}@example.com` }, "203.0.113.199");
        statuses.push(resp.status);
      }
    } finally {
      fetchSpy.mockRestore();
    }
    expect(statuses).toEqual([200, 200, 200, 200, 200, 429]);
  });

  it("a stale session on a taken address gets the same 403, so the refusal adds no account-existence oracle", async () => {
    const s = await subscriber("oracle");
    const taken = await subscriber("oracle-taken");
    await ageSession(s.sessionId, STALE);
    const onTaken = await post("/subscriber/change-email", s.cookie, { new_email: taken.email }, "203.0.113.195");
    const onFree = await post("/subscriber/change-email", s.cookie, { new_email: `sec67-free-${Date.now()}@example.com` }, "203.0.113.196");
    expect(onTaken.status).toBe(403);
    expect(onFree.status).toBe(403);
    expect(await onTaken.text()).toBe(await onFree.text());
  });

  it("sibling route: a stale session can still save a name, so only the identity swap is gated", async () => {
    const s = await subscriber("sibling");
    await ageSession(s.sessionId, STALE);
    const resp = await post("/subscriber/profile", s.cookie, { first_name: "Sam" }, "203.0.113.197");
    expect(resp.status).toBe(200);
  });
});
