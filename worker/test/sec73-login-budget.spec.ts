/**
 * SEC-73 (LOW, 2026-11-06): the magic-link forms accept a missing Turnstile
 * token, so a script that knew one address could spend that person's whole
 * hourly send budget from any IP, and their own request then got "check your
 * email" and nothing in the inbox. A request with no token now has its own
 * smaller budget. The ad-blocker path has to keep working, so these assert that
 * too. These do not show the finding is closed: someone who solves the challenge
 * can still spend the solved budget, and that case is pinned below.
 */
import { env } from "cloudflare:test";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as store from "../src/store";
import { slotsUsed } from "./rate-limit-slots";

const BASE = "https://deadline-radar.com";
const SECRET_ENV = { RESEND_API_KEY: "test-key-not-real", TURNSTILE_SECRET_KEY: "test-secret-not-real" };

afterEach(() => vi.restoreAllMocks());

/** Turnstile accepts only "good-token"; every other outbound call is the mail send. */
function mockOutbound(): void {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
    if (String(input).includes("turnstile")) {
      const sent = new URLSearchParams(init?.body as URLSearchParams);
      return new Response(JSON.stringify({ success: sent.get("response") === "good-token" }), { status: 200 });
    }
    return new Response("{}", { status: 202 });
  });
}

let ipSeq = 0;
async function post(path: string, fields: Record<string, string>): Promise<number> {
  const worker = (await import("../src/index")).default;
  const pending: Promise<unknown>[] = [];
  const ctx = { waitUntil: (p: Promise<unknown>) => pending.push(p), passThroughOnException() {}, props: {} } as unknown as ExecutionContext;
  ipSeq += 1;
  const response = await worker.fetch(
    new Request(`${BASE}${path}`, {
      method: "POST",
      headers: { "content-type": "application/x-www-form-urlencoded", "cf-connecting-ip": `198.18.73.${(ipSeq % 250) + 1}` },
      body: new URLSearchParams({ hp_website: "", ...fields }).toString(),
    }),
    { ...env, ...SECRET_ENV } as never,
    ctx
  );
  await Promise.allSettled(pending);
  return response.status;
}

async function count(sql: string, value: string): Promise<number> {
  const row = await env.DB.prepare(sql).bind(value).first<{ c: number }>();
  return row?.c ?? 0;
}

const ROUTES = [
  {
    name: "firm",
    path: "/firm/login",
    bucket: "firm_login_account",
    emailField: "admin_email",
    async seed(email: string): Promise<string> {
      const firm = await store.createFirm(env.DB, { name: "SEC-73 LLP", adminEmail: email });
      return firm.id;
    },
    links: (id: string) => count("SELECT COUNT(*) AS c FROM firm_login_tokens WHERE firm_id = ?1", id),
  },
  {
    name: "subscriber",
    path: "/subscriber/login",
    bucket: "subscriber_login_account",
    emailField: "email",
    async seed(email: string): Promise<string> {
      await store.addPending(env.DB, { email, stateSlug: "texas", deadlineFields: {}, firstName: null, skipConfirmation: true, firmId: null });
      return email.toLowerCase();
    },
    links: (id: string) => count("SELECT COUNT(*) AS c FROM subscriber_login_tokens WHERE email_normalized = ?1", id),
  },
];

describe.each(ROUTES)("SEC-73: $name magic link", (route) => {
  const key = (email: string) => `account:${store.normalizeEmail(email)}`;
  const tokenless = `${route.bucket}_tokenless`;

  it("draining the budget with no token leaves a solved request able to get a link", async () => {
    mockOutbound();
    const email = `sec73-${route.name}-drain-${Date.now()}@examplefirm.com`;
    const id = await route.seed(email);
    for (let i = 0; i < 5; i++) {
      expect(await post(route.path, { [route.emailField]: email })).toBe(200);
    }
    expect(await slotsUsed(key(email), tokenless)).toBe(3);
    expect(await slotsUsed(key(email), route.bucket)).toBe(0);
    expect(await route.links(id)).toBe(3);

    expect(await post(route.path, { [route.emailField]: email, "cf-turnstile-response": "good-token" })).toBe(200);
    expect(await slotsUsed(key(email), route.bucket)).toBe(1);
    expect(await route.links(id)).toBe(4);
  });

  it("an ad-blocked request with no token still gets a link", async () => {
    mockOutbound();
    const email = `sec73-${route.name}-adblock-${Date.now()}@examplefirm.com`;
    const id = await route.seed(email);
    expect(await post(route.path, { [route.emailField]: email })).toBe(200);
    expect(await route.links(id)).toBe(1);
  });

  it("an empty token field counts as no token", async () => {
    mockOutbound();
    const email = `sec73-${route.name}-empty-${Date.now()}@examplefirm.com`;
    await route.seed(email);
    expect(await post(route.path, { [route.emailField]: email, "cf-turnstile-response": "" })).toBe(200);
    expect(await slotsUsed(key(email), tokenless)).toBe(1);
    expect(await slotsUsed(key(email), route.bucket)).toBe(0);
  });

  it("a made-up token can't reach the solved budget", async () => {
    mockOutbound();
    const email = `sec73-${route.name}-fake-${Date.now()}@examplefirm.com`;
    const id = await route.seed(email);
    expect(await post(route.path, { [route.emailField]: email, "cf-turnstile-response": "made-up" })).toBe(400);
    expect(await slotsUsed(key(email), route.bucket)).toBe(0);
    expect(await slotsUsed(key(email), tokenless)).toBe(0);
    expect(await route.links(id)).toBe(0);
  });

  it("residual: solved requests are capped at five an hour, so five solves deny a sixth", async () => {
    mockOutbound();
    const email = `sec73-${route.name}-cap-${Date.now()}@examplefirm.com`;
    const id = await route.seed(email);
    for (let i = 0; i < 7; i++) {
      await post(route.path, { [route.emailField]: email, "cf-turnstile-response": "good-token" });
    }
    expect(await route.links(id)).toBe(5);
  });
});
