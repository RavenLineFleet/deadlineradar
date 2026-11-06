/**
 * SEC-74 (LOW, 2026-11-06): the SEC-71/72 reorder hoisted isEmailBlocklisted above
 * the rate-limit slot on five routes. It reads a global table, and the shared demo
 * session reaches three of them, so that made a free, unbounded blocklist oracle.
 * A blocklist probe has to spend a slot, and once the bucket is full a blocked
 * address and a clean one have to get the same 429.
 */
import { env } from "cloudflare:test";
import { describe, expect, it } from "vitest";
import * as store from "../src/store";
import worker from "../src/index";
import { RATE_LIMIT_FIRM_MEMBER_INVITE, RATE_LIMIT_SUBSCRIBER_CHANGE_EMAIL } from "../src/validation";

const BASE = "https://deadline-radar.com";

let seq = 0;
function uniqueEmail(label: string): string {
  seq += 1;
  return `sec74-${label}-${Date.now()}-${seq}@examplefirm.com`;
}

async function firmSession(): Promise<{ firmId: string; cookie: string }> {
  const f = await store.createFirm(env.DB, { name: "SEC-74 LLP", adminEmail: uniqueEmail("owner") });
  await env.DB.prepare("UPDATE firms SET plan_tier = 'firm_scale' WHERE id = ?1").bind(f.id).run();
  const { rawSessionToken } = await store.createSession(env.DB, f.id, f.memberId);
  return { firmId: f.id, cookie: `dr_firm_session=${rawSessionToken}` };
}

function send(method: string, path: string, cookie: string, body: unknown): Promise<Response> {
  seq += 1;
  const ctx = { waitUntil() {}, passThroughOnException() {}, props: {} } as unknown as ExecutionContext;
  return worker.fetch(
    new Request(`${BASE}${path}`, {
      method,
      headers: { "content-type": "application/json", "cf-connecting-ip": `198.51.100.${(seq % 250) + 1}`, Cookie: cookie, Origin: BASE },
      body: JSON.stringify(body),
    }),
    env as never,
    ctx
  );
}

async function block(address: string): Promise<void> {
  await env.DB.prepare("INSERT INTO signup_blocklist (id, pattern, pattern_type, note, created_at) VALUES (?1,?2,'email','test',?3)")
    .bind(crypto.randomUUID(), address.toLowerCase(), new Date().toISOString())
    .run();
}

async function slots(key: string, bucket: string): Promise<number> {
  const row = await env.DB.prepare("SELECT COUNT(*) AS n FROM rate_limit_hits WHERE ip = ?1 AND bucket = ?2").bind(key, bucket).first<{ n: number }>();
  return row?.n ?? 0;
}

async function fill(key: string, bucket: string, count: number): Promise<void> {
  const now = Math.floor(Date.now() / 1000);
  for (let i = 0; i < count; i += 1) {
    await env.DB.prepare("INSERT INTO rate_limit_hits (ip, bucket, ts) VALUES (?1,?2,?3)").bind(key, bucket, now).run();
  }
}

const INVITE_MAX = RATE_LIMIT_FIRM_MEMBER_INVITE.max;
const SUB_MAX = RATE_LIMIT_SUBSCRIBER_CHANGE_EMAIL.max;
const CREATE = { staff_label: "Staff", state_slug: "georgia", license_type_id: "ga-individual" };

/** A blocked address is refused and spends a slot; with the bucket full, blocked and clean look the same. */
async function assertMetered(key: string, bucket: string, max: number, probe: (address: string) => Promise<Response>): Promise<void> {
  const blocked = uniqueEmail("blocked");
  await block(blocked);
  const before = await slots(key, bucket);

  const refused = await probe(blocked);
  expect(refused.status).toBe(400);
  expect(await slots(key, bucket), `${bucket}: a blocklist probe must spend a slot`).toBe(before + 1);

  await fill(key, bucket, max - (before + 1));
  const blockedFull = await probe(blocked);
  const cleanFull = await probe(uniqueEmail("clean"));
  expect(blockedFull.status).toBe(429);
  expect(cleanFull.status).toBe(429);
  expect(await blockedFull.text()).toBe(await cleanFull.text());
}

describe("SEC-74: the blocklist read is metered", () => {
  it("change email", async () => {
    const f = await firmSession();
    await assertMetered(f.firmId, "firm_change_email", 5, (address) =>
      send("POST", "/firm/change-email", f.cookie, { new_email: address, current_password: "" })
    );
  });

  it("license create", async () => {
    const f = await firmSession();
    await assertMetered(f.firmId, "firm_license_create", 50, (address) =>
      send("POST", "/firm/licenses", f.cookie, { ...CREATE, email: address })
    );
  });

  it("license patch", async () => {
    const f = await firmSession();
    const created = await send("POST", "/firm/licenses", f.cookie, { ...CREATE, email: uniqueEmail("licensee") });
    expect(created.status).toBe(201);
    const { id } = await created.json<{ id: string }>();
    await assertMetered(f.firmId, "firm_license_patch", 50, (address) =>
      send("PATCH", `/firm/licenses/${id}`, f.cookie, { email: address })
    );
  });

  it("member invite", async () => {
    const f = await firmSession();
    await assertMetered(f.firmId, "firm_member_invite", INVITE_MAX, (address) =>
      send("POST", "/firm/members/invite", f.cookie, { email: address, role: "staff" })
    );
  });

  it("subscriber change email", async () => {
    const owner = store.normalizeEmail(uniqueEmail("sub"));
    const { rawSessionToken } = await store.createSubscriberSession(env.DB, owner);
    await assertMetered(owner, "subscriber_change_email", SUB_MAX, (address) =>
      send("POST", "/subscriber/change-email", `dr_sub_session=${rawSessionToken}`, { new_email: address })
    );
  });

  it("license patch: a row whose own address was blocked later can still be edited", async () => {
    const f = await firmSession();
    const email = uniqueEmail("later-blocked");
    const created = await send("POST", "/firm/licenses", f.cookie, { ...CREATE, email });
    expect(created.status).toBe(201);
    const { id } = await created.json<{ id: string }>();
    await block(email);
    const edited = await send("PATCH", `/firm/licenses/${id}`, f.cookie, { email, staff_label: "Renamed" });
    expect(edited.status).toBe(200);
  });
});
