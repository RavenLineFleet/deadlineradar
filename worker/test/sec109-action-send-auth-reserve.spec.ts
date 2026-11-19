/**
 * SEC-109 (SecurityLab MEDIUM, AuditLab CONFIRMED + WIDENED, 2026-11-19):
 * /assistant/ticket (no Turnstile, missing Origin passes) and /subscribe
 * (Turnstile token may be omitted, no origin gate) both spend the shared
 * action_send_counters day that also gates both magic-link senders. A
 * scripted client could drain it and every sign-in link was silently
 * dropped for the rest of the UTC day.
 *
 * Fix: the top third of the action cap is reserved for authCritical sends
 * (issueAndSendFirmLoginLink, issueAndSendSubscriberLoginLink). Everything
 * else stops at cap - floor(cap / 3). These arms seed the day's counter
 * directly and drive the real routes.
 */
import { env } from "cloudflare:test";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as store from "../src/store";
import {
  actionSendLimit,
  checkAndCountActionSend,
  DEFAULT_DAILY_ACTION_SEND_CAP,
} from "../src/sender";

const CAP = DEFAULT_DAILY_ACTION_SEND_CAP;
const NON_AUTH_LIMIT = CAP - Math.floor(CAP / 3);

let ipSeq = 0;
function freshIp(): string {
  ipSeq += 1;
  return `198.18.109.${(ipSeq % 250) + 1}`;
}

async function setActionCount(n: number): Promise<void> {
  await env.DB
    .prepare(
      `INSERT INTO action_send_counters (day, count) VALUES (strftime('%Y-%m-%d','now'), ?1)
       ON CONFLICT(day) DO UPDATE SET count = ?1`
    )
    .bind(n)
    .run();
}

async function actionCount(): Promise<number> {
  const row = await env.DB
    .prepare("SELECT count FROM action_send_counters WHERE day = strftime('%Y-%m-%d','now')")
    .first<{ count: number }>();
  return row?.count ?? 0;
}

async function workerFetch(request: Request): Promise<Response> {
  const worker = (await import("../src/index")).default;
  const pending: Promise<unknown>[] = [];
  const ctx = {
    waitUntil(p: Promise<unknown>) {
      pending.push(p);
    },
    passThroughOnException() {},
    props: {},
  } as unknown as ExecutionContext;
  const response = await worker.fetch(request, { ...env, RESEND_API_KEY: "test-key-not-real" } as never, ctx);
  await Promise.allSettled(pending);
  return response;
}

function spyOutbound() {
  return vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("{}", { status: 202 }));
}

function form(path: string, fields: Record<string, string>): Request {
  return new Request(`https://deadline-radar.com${path}`, {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded", "cf-connecting-ip": freshIp() },
    body: new URLSearchParams({ hp_website: "", ...fields }).toString(),
  });
}

function ticket(): Request {
  // No Origin header: the scripted-client shape SEC-109 reproduced live.
  return new Request("https://deadline-radar.com/api/assistant/ticket", {
    method: "POST",
    headers: { "content-type": "application/json", "cf-connecting-ip": freshIp() },
    body: JSON.stringify({ description: "SEC-109 drain", email: "visitor@example.com" }),
  });
}

function subscribe(): Request {
  return form("/subscribe", {
    email: `sec109-sub-${Date.now()}-${ipSeq}@example.com`,
    state: "georgia",
    license_type_id: "ga-individual",
  });
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("SEC-109: actionSendLimit", () => {
  it("reserves floor(cap/3) for authCritical sends only", () => {
    expect(actionSendLimit(300, false)).toBe(200);
    expect(actionSendLimit(300, true)).toBe(300);
    expect(actionSendLimit(10, false)).toBe(7);
    expect(actionSendLimit(2, false)).toBe(2);
  });

  it("cap 0 stays a kill switch for both kinds (CAP-1/CAP-2)", async () => {
    expect(actionSendLimit(0, false)).toBe(0);
    expect(actionSendLimit(0, true)).toBe(0);
    expect(await checkAndCountActionSend(env.DB, 0, { authCritical: true })).toBe(false);
  });
});

describe("SEC-109: checkAndCountActionSend at the reserve boundary", () => {
  it("a default send is refused at the limit; an authCritical send is not", async () => {
    await setActionCount(NON_AUTH_LIMIT);
    expect(await checkAndCountActionSend(env.DB, CAP)).toBe(false);
    expect(await actionCount()).toBe(NON_AUTH_LIMIT);
    expect(await checkAndCountActionSend(env.DB, CAP, { authCritical: true })).toBe(true);
    expect(await actionCount()).toBe(NON_AUTH_LIMIT + 1);
  });

  it("an authCritical send is still refused at the full cap", async () => {
    await setActionCount(CAP);
    expect(await checkAndCountActionSend(env.DB, CAP, { authCritical: true })).toBe(false);
    expect(await actionCount()).toBe(CAP);
  });
});

describe("SEC-109: the drain routes stop at the reserve, sign-in links do not", () => {
  it("control: /assistant/ticket still sends one below the limit", async () => {
    await setActionCount(NON_AUTH_LIMIT - 1);
    const spy = spyOutbound();
    const resp = await workerFetch(ticket());
    expect(resp.status).toBe(200);
    expect(spy).toHaveBeenCalledTimes(1);
    expect(await actionCount()).toBe(NON_AUTH_LIMIT);
  });

  it("/assistant/ticket 503s at the limit and sends nothing", async () => {
    await setActionCount(NON_AUTH_LIMIT);
    const spy = spyOutbound();
    const resp = await workerFetch(ticket());
    expect(resp.status).toBe(503);
    expect(spy).not.toHaveBeenCalled();
    expect(await actionCount()).toBe(NON_AUTH_LIMIT);
  });

  it("control: /subscribe sends its confirmation one below the limit", async () => {
    await setActionCount(NON_AUTH_LIMIT - 1);
    const spy = spyOutbound();
    const resp = await workerFetch(subscribe());
    expect(resp.status).toBe(200);
    expect(spy).toHaveBeenCalledTimes(1);
    expect(await actionCount()).toBe(NON_AUTH_LIMIT);
  });

  it("/subscribe (no Turnstile token) sends no confirmation at the limit", async () => {
    await setActionCount(NON_AUTH_LIMIT);
    const spy = spyOutbound();
    const resp = await workerFetch(subscribe());
    expect(resp.status).toBe(200);
    expect(spy).not.toHaveBeenCalled();
    expect(await actionCount()).toBe(NON_AUTH_LIMIT);
  });

  it("a firm sign-in link still goes out after the drain routes hit the limit", async () => {
    const adminEmail = `sec109-firm-${Date.now()}@examplefirm.com`;
    await store.createFirm(env.DB, { name: "SEC-109 LLP", adminEmail });
    await setActionCount(NON_AUTH_LIMIT);
    const spy = spyOutbound();
    const resp = await workerFetch(form("/firm/login", { admin_email: adminEmail }));
    expect(resp.status).toBe(200);
    expect(spy).toHaveBeenCalledTimes(1);
    expect(await actionCount()).toBe(NON_AUTH_LIMIT + 1);
  });

  it("a subscriber sign-in link still goes out after the drain routes hit the limit", async () => {
    const email = `sec109-subscriber-${Date.now()}@examplefirm.com`;
    await store.addPending(env.DB, { email, stateSlug: "texas", deadlineFields: {}, firstName: null, skipConfirmation: true, firmId: null });
    await setActionCount(NON_AUTH_LIMIT);
    const spy = spyOutbound();
    const resp = await workerFetch(form("/subscriber/login", { email }));
    expect(resp.status).toBe(200);
    expect(spy).toHaveBeenCalledTimes(1);
    expect(await actionCount()).toBe(NON_AUTH_LIMIT + 1);
  });

  it("the full cap still binds sign-in links", async () => {
    const adminEmail = `sec109-full-${Date.now()}@examplefirm.com`;
    await store.createFirm(env.DB, { name: "SEC-109 Full LLP", adminEmail });
    await setActionCount(CAP);
    const spy = spyOutbound();
    const resp = await workerFetch(form("/firm/login", { admin_email: adminEmail }));
    expect(resp.status).toBe(200);
    expect(spy).not.toHaveBeenCalled();
    expect(await actionCount()).toBe(CAP);
  });
});
