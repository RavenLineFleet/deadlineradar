/**
 * SILENT-6 (MEDIUM, 2026-11-06): a resend that never got delivered used to burn one
 * of a pending subscriber's 3 lifetime attempts, and nothing ever gave them back.
 * Now only a delivery counts, the cooldown is stamped either way, and a per-record
 * slot bounds failed attempts. These drive the real POST /subscribe path.
 */
import { env } from "cloudflare:test";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as store from "../src/store";
import type { SubscriberRow } from "../src/store";
import { RATE_LIMIT_SUBSCRIBE_RESEND } from "../src/validation";

afterEach(() => vi.restoreAllMocks());

let seq = 0;
function uniqueIp(): string {
  seq += 1;
  return `198.51.${Math.floor(seq / 250) + 60}.${(seq % 250) + 1}`;
}

async function pendingRow(label: string): Promise<SubscriberRow> {
  seq += 1;
  return store.addPending(env.DB, {
    email: `silent6-${label}-${Date.now()}-${seq}@example.com`,
    stateSlug: "georgia",
    deadlineFields: { license_type_id: "ga-individual" },
    firstName: null,
  });
}

async function repost(email: string): Promise<void> {
  const worker = (await import("../src/index")).default;
  const ctx = { waitUntil() {}, passThroughOnException() {}, props: {} } as unknown as ExecutionContext;
  const request = new Request("https://deadline-radar.com/subscribe", {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded", "cf-connecting-ip": uniqueIp() },
    body: new URLSearchParams({ hp_website: "", email, state: "georgia", license_type_id: "ga-individual" }).toString(),
  });
  const response = await worker.fetch(request, { ...env, RESEND_API_KEY: "test-key-not-real" } as never, ctx);
  expect(response.status).toBe(200);
}

async function read(id: string): Promise<SubscriberRow> {
  return (await env.DB.prepare("SELECT * FROM subscribers WHERE id = ?1").bind(id).first<SubscriberRow>())!;
}

async function pastCooldown(id: string): Promise<void> {
  const old = new Date(Date.now() - (store.RESEND_COOLDOWN_MINUTES + 1) * 60_000).toISOString();
  await env.DB.prepare("UPDATE subscribers SET last_resend_at = ?1 WHERE id = ?2").bind(old, id).run();
}

async function pastSlotWindow(id: string): Promise<void> {
  await env.DB
    .prepare("UPDATE rate_limit_hits SET ts = ts - ?1 WHERE ip = ?2 AND bucket = 'subscribe_resend'")
    .bind(RATE_LIMIT_SUBSCRIBE_RESEND.windowSeconds + 1, id)
    .run();
}

async function actionCount(): Promise<number> {
  const row = await env.DB
    .prepare("SELECT count FROM action_send_counters WHERE day = strftime('%Y-%m-%d','now')")
    .first<{ count: number }>();
  return row?.count ?? 0;
}

function mailApi(status: number) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response("{}", { status }));
}

describe("a resend that wasn't delivered", () => {
  it("starts the cooldown but doesn't use up an attempt", async () => {
    const row = await pendingRow("failed");
    const api = mailApi(500);
    await repost(row.email);
    expect(api).toHaveBeenCalledTimes(1);
    const after = await read(row.id);
    expect(after.resend_count).toBe(0);
    expect(after.last_resend_at).toBeTruthy();

    await repost(row.email);
    expect(api).toHaveBeenCalledTimes(1);
  });

  it("counts the same when the mail API can't be reached at all", async () => {
    const row = await pendingRow("thrown");
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("network down"));
    await repost(row.email);
    const after = await read(row.id);
    expect(after.resend_count).toBe(0);
    expect(after.last_resend_at).toBeTruthy();
  });

  it("stops at the per-record bound, and the refused attempt charges nothing global", async () => {
    const row = await pendingRow("bounded");
    const api = mailApi(500);
    for (let i = 0; i < RATE_LIMIT_SUBSCRIBE_RESEND.max; i++) {
      await repost(row.email);
      await pastCooldown(row.id);
    }
    expect(api).toHaveBeenCalledTimes(RATE_LIMIT_SUBSCRIBE_RESEND.max);

    const before = await actionCount();
    await repost(row.email);
    expect(api).toHaveBeenCalledTimes(RATE_LIMIT_SUBSCRIBE_RESEND.max);
    expect(await actionCount()).toBe(before);
  });

  it("never blocks someone retrying about once a day, however long the outage lasts", async () => {
    const row = await pendingRow("daily");
    const api = mailApi(500);
    const attempts = RATE_LIMIT_SUBSCRIBE_RESEND.max * 2;
    for (let i = 0; i < attempts; i++) {
      await repost(row.email);
      await env.DB
        .prepare("UPDATE rate_limit_hits SET ts = ts - ?1 WHERE ip = ?2 AND bucket = 'subscribe_resend'")
        .bind(23 * 3600, row.id)
        .run();
      await pastCooldown(row.id);
    }
    expect(api).toHaveBeenCalledTimes(attempts);
    expect((await read(row.id)).resend_count).toBe(0);
  });

  it("leaves the subscriber able to get a real email once the outage is over", async () => {
    const row = await pendingRow("heals");
    const down = mailApi(500);
    for (let i = 0; i < RATE_LIMIT_SUBSCRIBE_RESEND.max; i++) {
      await repost(row.email);
      await pastCooldown(row.id);
    }
    expect((await read(row.id)).resend_count).toBe(0);
    down.mockRestore();

    await pastSlotWindow(row.id);
    const up = mailApi(202);
    await repost(row.email);
    expect(up).toHaveBeenCalledTimes(1);
    expect((await read(row.id)).resend_count).toBe(1);
  });
});

describe("a resend that was delivered", () => {
  it("still counts, and the lifetime cap holds after the per-record window clears", async () => {
    const row = await pendingRow("delivered");
    const api = mailApi(202);
    for (let i = 0; i < store.RESEND_MAX_ATTEMPTS; i++) {
      await repost(row.email);
      await pastCooldown(row.id);
    }
    expect(api).toHaveBeenCalledTimes(store.RESEND_MAX_ATTEMPTS);
    expect((await read(row.id)).resend_count).toBe(store.RESEND_MAX_ATTEMPTS);

    await pastSlotWindow(row.id);
    await repost(row.email);
    expect(api).toHaveBeenCalledTimes(store.RESEND_MAX_ATTEMPTS);
  });
});
