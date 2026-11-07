/**
 * SEC-78 + SEC-79 (LOW, 2026-11-06): a firm's 24h budget for the two
 * partner-triggered sends pays for email going out. A full global cap, or a state
 * with nobody on the roster, sends nothing and costs nothing. Once any recipient
 * has been delivered to, the slot stays spent even if the cap refuses the rest.
 */
import { env } from "cloudflare:test";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as store from "../src/store";

const BASE = "https://deadline-radar.com";
const CPE = "firm_staff_cpe_reminder";
const NOTIFY = "firm_rule_change_notify";
const CAP = 50;
const NOTIFY_MAX = 20;
const RULE = { state_slug: "georgia", jurisdiction: "Georgia", summary: "A change.", effective_date_label: "Jan 1, 2027" };

// Creating a roster row mails the staffer too, so no request here reaches the network.
beforeEach(() => {
  mailApi(200);
});

afterEach(async () => {
  vi.restoreAllMocks();
  await setActionCount(0);
});

let seq = 0;
function uniqueEmail(label: string): string {
  seq += 1;
  return `sec78-${label}-${Date.now()}-${seq}@examplefirm.com`;
}

async function setActionCount(count: number): Promise<void> {
  await env.DB
    .prepare(
      `INSERT INTO action_send_counters (day, count) VALUES (strftime('%Y-%m-%d','now'), ?1)
       ON CONFLICT(day) DO UPDATE SET count = ?1`
    )
    .bind(count)
    .run();
}

async function post(path: string, cookie: string, body: unknown): Promise<Response> {
  const worker = (await import("../src/index")).default;
  const ctx = { waitUntil() {}, passThroughOnException() {}, props: {} } as unknown as ExecutionContext;
  const request = new Request(`${BASE}${path}`, {
    method: "POST",
    headers: { "content-type": "application/json", Cookie: cookie, Origin: BASE },
    body: JSON.stringify(body),
  });
  return worker.fetch(request, { ...env, RESEND_API_KEY: "test-key-not-real", ACTION_DAILY_SEND_CAP: String(CAP) } as never, ctx);
}

async function firm(): Promise<{ firmId: string; cookie: string }> {
  const f = await store.createFirm(env.DB, { name: "SEC-78 LLP", adminEmail: uniqueEmail("owner") });
  await env.DB.prepare("UPDATE firms SET plan_tier = 'firm_scale' WHERE id = ?1").bind(f.id).run();
  const { rawSessionToken } = await store.createSession(env.DB, f.id, f.memberId);
  return { firmId: f.id, cookie: `dr_firm_session=${rawSessionToken}` };
}

async function staff(cookie: string): Promise<string> {
  const created = await post("/firm/licenses", cookie, {
    staff_label: "Staff",
    email: uniqueEmail("staff"),
    state_slug: "georgia",
    license_type_id: "ga-individual",
  });
  expect(created.status).toBe(201);
  return (await created.json<{ id: string }>()).id;
}

async function slots(firmId: string, bucket: string): Promise<number> {
  const row = await env.DB
    .prepare("SELECT COUNT(*) AS n FROM rate_limit_hits WHERE ip = ?1 AND bucket = ?2")
    .bind(firmId, bucket)
    .first<{ n: number }>();
  return row?.n ?? 0;
}

function mailApi(status: number) {
  return vi.spyOn(globalThis, "fetch").mockClear().mockImplementation(async () => new Response("{}", { status }));
}

interface NotifyBody {
  sent: number;
  skipped: number;
  total: number;
}

describe("staff CPE reminder refused by the global cap (SEC-78)", () => {
  it("gives the firm its slot back and sends once the cap resets", async () => {
    const f = await firm();
    const id = await staff(f.cookie);
    const api = mailApi(200);
    await setActionCount(CAP);

    for (let i = 0; i < 3; i++) {
      const resp = await post("/firm/staff-cpe-reminder", f.cookie, { subscriber_id: id });
      expect(resp.status).toBe(200);
      const body = await resp.json<{ sent: boolean; reason: string }>();
      expect(body.sent).toBe(false);
      expect(body.reason).toContain("email limit");
    }
    expect(api).not.toHaveBeenCalled();
    expect(await slots(f.firmId, CPE)).toBe(0);

    await setActionCount(0);
    const resp = await post("/firm/staff-cpe-reminder", f.cookie, { subscriber_id: id });
    expect((await resp.json<{ sent: boolean }>()).sent).toBe(true);
    expect(await slots(f.firmId, CPE)).toBe(1);
  });

  it("leaves slots from earlier real sends in place", async () => {
    const f = await firm();
    const id = await staff(f.cookie);
    mailApi(200);
    await post("/firm/staff-cpe-reminder", f.cookie, { subscriber_id: id });
    expect(await slots(f.firmId, CPE)).toBe(1);

    await setActionCount(CAP);
    await post("/firm/staff-cpe-reminder", f.cookie, { subscriber_id: id });
    expect(await slots(f.firmId, CPE)).toBe(1);
  });

  it("keeps the slot when the send was attempted and failed", async () => {
    const f = await firm();
    const id = await staff(f.cookie);
    mailApi(500);
    const resp = await post("/firm/staff-cpe-reminder", f.cookie, { subscriber_id: id });
    expect((await resp.json<{ sent: boolean }>()).sent).toBe(false);
    expect(await slots(f.firmId, CPE)).toBe(1);
  });
});

describe("rule-change notify refused by the global cap (SEC-78)", () => {
  it("gives the slot back when the cap refuses the first recipient", async () => {
    const f = await firm();
    await staff(f.cookie);
    await staff(f.cookie);
    const api = mailApi(200);
    await setActionCount(CAP);

    const resp = await post("/firm/rule-change/notify", f.cookie, RULE);
    expect(await resp.json<NotifyBody>()).toMatchObject({ sent: 0, skipped: 2, total: 2 });
    expect(api).not.toHaveBeenCalled();
    expect(await slots(f.firmId, NOTIFY)).toBe(0);
  });

  it("keeps the slot when the cap refuses after a recipient was delivered to", async () => {
    const f = await firm();
    await staff(f.cookie);
    await staff(f.cookie);
    const api = mailApi(200);
    await setActionCount(CAP - 1);

    const resp = await post("/firm/rule-change/notify", f.cookie, RULE);
    expect(await resp.json<NotifyBody>()).toMatchObject({ sent: 1, skipped: 1, total: 2 });
    expect(api).toHaveBeenCalledTimes(1);
    expect(await slots(f.firmId, NOTIFY)).toBe(1);
  });

  it("gives the slot back when the only attempt before the cap wasn't delivered", async () => {
    const f = await firm();
    await staff(f.cookie);
    await staff(f.cookie);
    const api = mailApi(500);
    await setActionCount(CAP - 1);

    const resp = await post("/firm/rule-change/notify", f.cookie, RULE);
    expect(await resp.json<NotifyBody>()).toMatchObject({ sent: 0, skipped: 2, total: 2 });
    expect(api).toHaveBeenCalled();
    expect(await slots(f.firmId, NOTIFY)).toBe(0);
  });

  it("keeps the slot when every recipient was attempted and the cap never refused", async () => {
    const f = await firm();
    await staff(f.cookie);
    mailApi(500);
    const resp = await post("/firm/rule-change/notify", f.cookie, RULE);
    expect(await resp.json<NotifyBody>()).toMatchObject({ sent: 0, skipped: 1, total: 1 });
    expect(await slots(f.firmId, NOTIFY)).toBe(1);
  });
});

describe("rule-change notify for a state nobody on the roster holds (SEC-79)", () => {
  it.each([
    ["a slug that isn't a state", "zz"],
    ["a real state the firm has no staff in", "alabama"],
  ])("%s spends no slot, however many times it's posted", async (_label, state_slug) => {
    const f = await firm();
    await staff(f.cookie);
    const api = mailApi(200);

    for (let i = 0; i < NOTIFY_MAX + 2; i++) {
      const resp = await post("/firm/rule-change/notify", f.cookie, { ...RULE, state_slug });
      expect(resp.status).toBe(200);
      expect(await resp.json<NotifyBody>()).toMatchObject({ sent: 0, skipped: 0, total: 0 });
    }
    expect(api).not.toHaveBeenCalled();
    expect(await slots(f.firmId, NOTIFY)).toBe(0);

    const real = await post("/firm/rule-change/notify", f.cookie, RULE);
    expect(await real.json<NotifyBody>()).toMatchObject({ sent: 1, total: 1 });
    expect(await slots(f.firmId, NOTIFY)).toBe(1);
  });

  it("still meets the limiter once there's someone to send to", async () => {
    const f = await firm();
    await staff(f.cookie);
    const api = mailApi(200);

    for (let i = 0; i < NOTIFY_MAX; i++) {
      expect((await post("/firm/rule-change/notify", f.cookie, RULE)).status).toBe(200);
    }
    expect(await slots(f.firmId, NOTIFY)).toBe(NOTIFY_MAX);
    api.mockClear();

    const refused = await post("/firm/rule-change/notify", f.cookie, RULE);
    expect(refused.status).toBe(429);
    expect(api).not.toHaveBeenCalled();
    expect(await slots(f.firmId, NOTIFY)).toBe(NOTIFY_MAX);
  });

  // The one response the reorder changed: this was a 429 while the limiter sat above the roster read.
  it("answers 200, not 429, for an empty state once the budget is used up", async () => {
    const f = await firm();
    await staff(f.cookie);
    const api = mailApi(200);

    for (let i = 0; i < NOTIFY_MAX; i++) {
      expect((await post("/firm/rule-change/notify", f.cookie, RULE)).status).toBe(200);
    }
    expect(await slots(f.firmId, NOTIFY)).toBe(NOTIFY_MAX);
    api.mockClear();

    const empty = await post("/firm/rule-change/notify", f.cookie, { ...RULE, state_slug: "alabama" });
    expect(empty.status).toBe(200);
    expect(await empty.json<NotifyBody>()).toMatchObject({ sent: 0, skipped: 0, total: 0 });
    expect(api).not.toHaveBeenCalled();
    expect(await slots(f.firmId, NOTIFY)).toBe(NOTIFY_MAX);
  });
});
