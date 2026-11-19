/**
 * SEC-109-R1/R2 (SecurityLab) + AuditLab's detection-notice class
 * (2026-11-19). The reserved top third of the action cap was drainable
 * through its own sign-in routes (self-confirmed subscriber mailboxes, one
 * IP, ~3h), and only 2 of the 12 auth/recovery/detection sends could spend
 * it, so the "your sessions were ended" style notices stopped at 200.
 *
 * Fix: each authCritical send names a class (firm_session / firm_link /
 * subscriber / operator) with its own daily admission share
 * (auth_send_counters, migration 0094). The shares split the reserve and
 * never sum past it. These arms seed the counters directly and drive
 * the real routes.
 */
import { env } from "cloudflare:test";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as store from "../src/store";
import {
  ACTION_SEND_AUTH_RESERVE_DIVISOR,
  AUTH_SEND_CLASS_TENTHS,
  type AuthSendClass,
  authSendClassLimit,
  checkAndCountActionSend,
  DEFAULT_DAILY_ACTION_SEND_CAP,
} from "../src/sender";

const CAP = DEFAULT_DAILY_ACTION_SEND_CAP;
const NON_AUTH_LIMIT = CAP - Math.floor(CAP / ACTION_SEND_AUTH_RESERVE_DIVISOR);
const CLASSES = Object.keys(AUTH_SEND_CLASS_TENTHS) as AuthSendClass[];
const BASE = "https://deadline-radar.com";

let seq = 0;
function freshIp(): string {
  seq += 1;
  return `198.18.${Math.floor(seq / 250) + 110}.${(seq % 250) + 1}`;
}
function uniqueEmail(label: string): string {
  seq += 1;
  return `sec109r1-${label}-${Date.now()}-${seq}@examplefirm.com`;
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
async function setClassCount(cls: AuthSendClass, n: number): Promise<void> {
  await env.DB
    .prepare(
      `INSERT INTO auth_send_counters (day, class, count) VALUES (strftime('%Y-%m-%d','now'), ?1, ?2)
       ON CONFLICT(day, class) DO UPDATE SET count = ?2`
    )
    .bind(cls, n)
    .run();
}
async function classCount(cls: AuthSendClass): Promise<number> {
  const row = await env.DB
    .prepare("SELECT count FROM auth_send_counters WHERE day = strftime('%Y-%m-%d','now') AND class = ?1")
    .bind(cls)
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
  return new Request(`${BASE}${path}`, {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded", "cf-connecting-ip": freshIp() },
    body: new URLSearchParams({ hp_website: "", ...fields }).toString(),
  });
}

async function firmLogin(): Promise<Request> {
  const adminEmail = uniqueEmail("firm");
  await store.createFirm(env.DB, { name: "SEC-109-R1 LLP", adminEmail });
  return form("/firm/login", { admin_email: adminEmail });
}
async function subscriberLogin(): Promise<Request> {
  const email = uniqueEmail("subscriber");
  await store.addPending(env.DB, { email, stateSlug: "texas", deadlineFields: {}, firstName: null, skipConfirmation: true, firmId: null });
  return form("/subscriber/login", { email });
}
/** A firm member with two sessions; ending the other one sends the
 * "your other sessions were ended" detection notice (class firm_session). */
async function signOutOtherDevices(): Promise<Request> {
  const f = await store.createFirm(env.DB, { name: "SEC-109-R1 Notice LLP", adminEmail: uniqueEmail("owner") });
  await env.DB.prepare("UPDATE firms SET plan_tier = 'firm_starter' WHERE id = ?1").bind(f.id).run();
  const { rawSessionToken } = await store.createSession(env.DB, f.id, f.memberId);
  await store.createSession(env.DB, f.id, f.memberId);
  return new Request(`${BASE}/firm/sign-out-other-devices`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "cf-connecting-ip": freshIp(),
      Cookie: `dr_firm_session=${rawSessionToken}`,
      Origin: BASE,
    },
    body: "{}",
  });
}

beforeEach(async () => {
  // Storage carries across arms in this file; every arm seeds from zero.
  await env.DB.prepare("DELETE FROM auth_send_counters").run();
  await env.DB.prepare("DELETE FROM action_send_counters").run();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("SEC-109-R1: class shares", () => {
  it("split the default reserve 30 / 30 / 30 / 10", () => {
    expect(authSendClassLimit(CAP, "firm_session")).toBe(30);
    expect(authSendClassLimit(CAP, "firm_link")).toBe(30);
    expect(authSendClassLimit(CAP, "subscriber")).toBe(30);
    expect(authSendClassLimit(CAP, "operator")).toBe(10);
  });

  it("tenths sum to exactly 10, and the limits sum to at most the reserve for every cap from 12 to 5000", () => {
    expect(CLASSES.reduce((n, c) => n + AUTH_SEND_CLASS_TENTHS[c], 0)).toBe(10);
    for (let cap = 12; cap <= 5000; cap++) {
      const sum = CLASSES.reduce((n, c) => n + authSendClassLimit(cap, c), 0);
      expect(sum, `cap ${cap}`).toBeLessThanOrEqual(Math.floor(cap / ACTION_SEND_AUTH_RESERVE_DIVISOR));
    }
  });

  it("never sum past the reserve for any cap from 3 to 5000", () => {
    for (let cap = 3; cap <= 5000; cap++) {
      const sum = CLASSES.reduce((n, c) => n + authSendClassLimit(cap, c), 0);
      expect(sum, `cap ${cap}`).toBeLessThanOrEqual(Math.max(Math.floor(cap / ACTION_SEND_AUTH_RESERVE_DIVISOR), CLASSES.length));
    }
  });

  it("cap 0 stays a kill switch; a tiny positive cap still admits 1 per class", async () => {
    for (const c of CLASSES) {
      expect(authSendClassLimit(0, c)).toBe(0);
      expect(authSendClassLimit(1, c)).toBe(1);
      expect(await checkAndCountActionSend(env.DB, 0, { authCritical: c })).toBe(false);
    }
  });
});

describe("SEC-109-R1: admission at the helper", () => {
  it("a class at its share is refused and spends nothing on the shared row", async () => {
    await setActionCount(10);
    await setClassCount("subscriber", authSendClassLimit(CAP, "subscriber"));
    expect(await checkAndCountActionSend(env.DB, CAP, { authCritical: "subscriber" })).toBe(false);
    expect(await actionCount()).toBe(10);
    expect(await classCount("subscriber")).toBe(authSendClassLimit(CAP, "subscriber"));
  });

  it("one below its share is admitted and spends both rows", async () => {
    await setActionCount(10);
    await setClassCount("subscriber", authSendClassLimit(CAP, "subscriber") - 1);
    expect(await checkAndCountActionSend(env.DB, CAP, { authCritical: "subscriber" })).toBe(true);
    expect(await actionCount()).toBe(11);
    expect(await classCount("subscriber")).toBe(authSendClassLimit(CAP, "subscriber"));
  });

  it("operator: non-auth parked at its line, the operator unit lands above it", async () => {
    await setActionCount(NON_AUTH_LIMIT);
    expect(await checkAndCountActionSend(env.DB, CAP)).toBe(false);
    expect(await checkAndCountActionSend(env.DB, CAP, { authCritical: "operator" })).toBe(true);
    expect(await actionCount()).toBe(NON_AUTH_LIMIT + 1);
    expect(await classCount("operator")).toBe(1);
  });

  it("a non-auth send stops at the general pool line and never touches auth_send_counters", async () => {
    await setActionCount(NON_AUTH_LIMIT - 1);
    expect(await checkAndCountActionSend(env.DB, CAP)).toBe(true);
    expect(await checkAndCountActionSend(env.DB, CAP)).toBe(false);
    expect(await actionCount()).toBe(NON_AUTH_LIMIT);
    for (const c of CLASSES) expect(await classCount(c)).toBe(0);
  });

  it("a non-auth send never touches auth_send_counters", async () => {
    await setActionCount(10);
    expect(await checkAndCountActionSend(env.DB, CAP)).toBe(true);
    for (const c of CLASSES) expect(await classCount(c)).toBe(0);
  });

  it("worst case: non-auth at its limit plus three classes full, the fourth class's last unit still lands", async () => {
    const fs = authSendClassLimit(CAP, "firm_session");
    const fl = authSendClassLimit(CAP, "firm_link");
    const sub = authSendClassLimit(CAP, "subscriber");
    const op = authSendClassLimit(CAP, "operator");
    await setActionCount(NON_AUTH_LIMIT + fl + sub + fs + op - 1);
    await setClassCount("firm_link", fl);
    await setClassCount("subscriber", sub);
    await setClassCount("firm_session", fs);
    await setClassCount("operator", op - 1);
    expect(await checkAndCountActionSend(env.DB, CAP, { authCritical: "operator" })).toBe(true);
    expect(await actionCount()).toBe(NON_AUTH_LIMIT + fl + sub + fs + op);
    expect(await actionCount()).toBeLessThanOrEqual(CAP);
  });
});

describe("SEC-109-R1: draining one class through its own route can't reach another", () => {
  it("subscriber class drained: subscriber sign-in refused", async () => {
    await setClassCount("subscriber", authSendClassLimit(CAP, "subscriber"));
    const req = await subscriberLogin();
    const spy = spyOutbound();
    expect((await workerFetch(req)).status).toBe(200);
    expect(spy).not.toHaveBeenCalled();
  });

  it("subscriber class drained AND non-auth at its limit: firm sign-in link still goes out", async () => {
    await setClassCount("subscriber", authSendClassLimit(CAP, "subscriber"));
    await setActionCount(NON_AUTH_LIMIT + authSendClassLimit(CAP, "subscriber"));
    const req = await firmLogin();
    const spy = spyOutbound();
    expect((await workerFetch(req)).status).toBe(200);
    expect(spy).toHaveBeenCalledTimes(1);
    expect(await classCount("firm_link")).toBe(1);
  });

  it("both sign-in classes drained AND non-auth at its limit: the sessions-ended notice still goes out", async () => {
    const fl = authSendClassLimit(CAP, "firm_link");
    const sub = authSendClassLimit(CAP, "subscriber");
    await setClassCount("firm_link", fl);
    await setClassCount("subscriber", sub);
    await setActionCount(NON_AUTH_LIMIT + fl + sub);
    const req = await signOutOtherDevices();
    const spy = spyOutbound();
    const resp = await workerFetch(req);
    expect(resp.status).toBe(200);
    expect(spy).toHaveBeenCalledTimes(1);
    expect(await classCount("firm_session")).toBe(1);
  });

  it("control: firm_session drained, the sessions-ended notice is refused", async () => {
    await setClassCount("firm_session", authSendClassLimit(CAP, "firm_session"));
    const req = await signOutOtherDevices();
    const spy = spyOutbound();
    expect((await workerFetch(req)).status).toBe(200);
    expect(spy).not.toHaveBeenCalled();
  });
});
