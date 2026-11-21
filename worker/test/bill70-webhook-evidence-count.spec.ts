/**
 * BILL-70 (AuditLab 2026-11-20, MEDIUM): the daily webhook evidence rows were
 * one bit per UTC day (INSERT ... ON CONFLICT DO NOTHING). A control POST at
 * 20:49Z claimed the day's stripe_sig_fail row, so a real Stripe delivery to
 * a wrong-secret endpoint at 20:54Z would have left no trace at all. Each
 * evidence row now counts (hit_count, last_seen_at; migration 0095), so
 * "1 control + 1 live delivery" reads as 2. Email-claim rows keep their
 * do-nothing-on-conflict shape.
 */
import { env } from "cloudflare:test";
import { describe, expect, it, vi } from "vitest";
import * as store from "../src/store";

function testExecutionContext(): ExecutionContext {
  return { waitUntil() {}, passThroughOnException() {}, props: {} } as unknown as ExecutionContext;
}

async function workerFetch(request: Request, envOverrides: Record<string, unknown> = {}): Promise<Response> {
  const worker = (await import("../src/index")).default;
  return worker.fetch(request, { ...env, ...envOverrides } as never, testExecutionContext());
}

async function signPayload(secret: string, timestampSeconds: number, payload: string): Promise<string> {
  const key = await crypto.subtle.importKey("raw", new TextEncoder().encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const sigBuffer = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(`${timestampSeconds}.${payload}`));
  const hex = [...new Uint8Array(sigBuffer)].map((b) => b.toString(16).padStart(2, "0")).join("");
  return `t=${timestampSeconds},v1=${hex}`;
}

async function postWebhook(payload: string, sigHeader: string | null, envOverrides: Record<string, unknown>): Promise<Response> {
  const headers: Record<string, string> = { "content-type": "application/json" };
  if (sigHeader) headers["Stripe-Signature"] = sigHeader;
  return workerFetch(new Request("https://deadline-radar.com/stripe/webhook", { method: "POST", headers, body: payload }), envOverrides);
}

const SECRET = "whsec_bill70_installed";
const LIVE_ENV = { STRIPE_SECRET_KEY: "sk_live_x", STRIPE_WEBHOOK_SECRET: SECRET, SEND_APPROVED_PASSES: "" };
const day = () => new Date().toISOString().slice(0, 10);

type EvidenceRow = { n: number; hit_count: number | null; received_at: string | null; last_seen_at: string | null };
async function evidence(id: string): Promise<EvidenceRow> {
  const row = await env.DB.prepare(
    "SELECT COUNT(*) AS n, MAX(hit_count) AS hit_count, MAX(received_at) AS received_at, MAX(last_seen_at) AS last_seen_at FROM stripe_webhook_events WHERE id = ?1"
  )
    .bind(id)
    .first<EvidenceRow>();
  return row ?? { n: 0, hit_count: null, received_at: null, last_seen_at: null };
}
async function clear(...ids: string[]): Promise<void> {
  for (const id of ids) {
    await env.DB.prepare("DELETE FROM stripe_webhook_events WHERE id = ?1 OR id LIKE ?1 || ':%'").bind(id).run();
  }
}

describe("BILL-70: webhook evidence rows count every hit, not just the first of the day", () => {
  it("the AuditLab scenario: a junk-signed control POST, then a Stripe-signed delivery for another endpoint, reads 2 on one row", async () => {
    const id = `stripe_sig_fail:${day()}`;
    await clear(id, `stripe_sig_fail_alert:${day()}`);
    const control = JSON.stringify({ id: "evt_bill70_control", type: "x", data: { object: {} } });
    expect((await postWebhook(control, "t=1,v1=deadbeef", LIVE_ENV)).status).toBe(400);
    const afterControl = await evidence(id);
    expect(afterControl.n).toBe(1);
    expect(afterControl.hit_count).toBe(1);

    const live = JSON.stringify({ id: "evt_bill70_live", type: "invoice.created", livemode: true, data: { object: {} } });
    const sig = await signPayload("whsec_some_other_endpoint", Math.floor(Date.now() / 1000), live);
    expect((await postWebhook(live, sig, LIVE_ENV)).status).toBe(400);
    const afterLive = await evidence(id);
    expect(afterLive.n).toBe(1);
    expect(afterLive.hit_count).toBe(2);
    expect(afterLive.received_at).toBe(afterControl.received_at);
    expect(afterLive.last_seen_at! >= afterControl.last_seen_at!).toBe(true);
  });

  it("the day's email-claim row is untouched by the count (no hit_count), so its once-a-day claim still holds", async () => {
    const id = `stripe_sig_fail:${day()}`;
    const alertId = `stripe_sig_fail_alert:${day()}`;
    await clear(id, alertId);
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response(JSON.stringify({ id: "em_1" }), { status: 200 }));
    try {
      const ENV = { ...LIVE_ENV, RESEND_API_KEY: "re_test", SEND_APPROVED_PASSES: "webhookSignatureFailureAlert" };
      for (let i = 0; i < 4; i++) {
        const p = JSON.stringify({ id: `evt_bill70_alert_${i}`, type: "x", livemode: true, data: { object: {} } });
        expect((await postWebhook(p, await signPayload("whsec_other", Math.floor(Date.now() / 1000), p), ENV)).status).toBe(400);
      }
      const sends = fetchSpy.mock.calls.filter((c) => String(c[0] instanceof Request ? c[0].url : c[0]).includes("resend"));
      expect(sends.length).toBe(1);
      expect((await evidence(id)).hit_count).toBe(4);
      const alert = await evidence(alertId);
      expect(alert.n).toBe(1);
      expect(alert.hit_count).toBeNull();
    } finally {
      fetchSpy.mockRestore();
    }
  });

  it("a row written earlier the same day by pre-0095 code (hit_count NULL) counts as 1, so the next hit reads 2", async () => {
    const id = `stripe_sig_fail:bill70-legacy-${Math.random().toString(36).slice(2)}`;
    expect(await store.recordDailyWebhookMarkerIfNew(env.DB, id, "stripe_sig_fail")).toBe(true);
    expect((await evidence(id)).hit_count).toBeNull();
    await store.bumpDailyWebhookEvidenceRow(env.DB, id, "stripe_sig_fail");
    expect((await evidence(id)).hit_count).toBe(2);
  });

  it("the other three evidence rows count too: mode reject, drop, handler throw", async () => {
    const TEST_ENV = { STRIPE_SECRET_KEY: "sk_test_x", STRIPE_WEBHOOK_SECRET: SECRET };
    const modeId = `stripe_mode_reject:${day()}`;
    const dropId = `stripe_drop:${day()}`;
    const throwId = `stripe_handler_throw:${day()}`;
    await clear(modeId, dropId, throwId);
    const t = Math.floor(Date.now() / 1000);
    for (let i = 0; i < 2; i++) {
      const p = JSON.stringify({ id: `evt_bill70_mode_${i}_${Math.random().toString(36).slice(2)}`, type: "invoice.created", livemode: true, data: { object: {} } });
      expect((await postWebhook(p, await signPayload(SECRET, t, p), TEST_ENV)).status).toBe(400);
    }
    for (const body of ["not json {", "also not json ["]) {
      expect((await postWebhook(body, await signPayload(SECRET, t, body), TEST_ENV)).status).toBe(400);
    }
    expect((await evidence(modeId)).hit_count).toBe(2);
    expect((await evidence(dropId)).hit_count).toBe(2);
    const errSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    try {
      const { noteWebhookHandlerThrow } = await import("../src/subscription_sync");
      await noteWebhookHandlerThrow(env as never, new Error("bill70 a"));
      await noteWebhookHandlerThrow(env as never, new Error("bill70 b"));
      await noteWebhookHandlerThrow(env as never, new Error("bill70 c"));
    } finally {
      errSpy.mockRestore();
    }
    const thrown = await evidence(throwId);
    expect(thrown.n).toBe(1);
    expect(thrown.hit_count).toBe(3);
  });

  it("positive control: a correctly-signed live event leaves no sig-fail row and does not bump an existing one", async () => {
    const id = `stripe_sig_fail:${day()}`;
    await clear(id, `stripe_sig_fail_alert:${day()}`);
    const junk = JSON.stringify({ id: "evt_bill70_junk", type: "x", data: { object: {} } });
    expect((await postWebhook(junk, "t=1,v1=00", LIVE_ENV)).status).toBe(400);
    const ok = JSON.stringify({ id: `evt_bill70_ok_${Date.now()}`, type: "invoice.created", livemode: true, data: { object: {} } });
    expect((await postWebhook(ok, await signPayload(SECRET, Math.floor(Date.now() / 1000), ok), LIVE_ENV)).status).toBe(200);
    expect((await evidence(id)).hit_count).toBe(1);
  });
});
