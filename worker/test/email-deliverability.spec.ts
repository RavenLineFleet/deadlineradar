/**
 * Roadmap #55 (2026-08-09, bounce/complaint tracking), Resend/Svix webhook
 * (replaces the old SendGrid Event Webhook -- Orchestrator directive,
 * 2026-09-23, Devin: "Replace SendGrid completely"). Two layers tested
 * independently, same split as resend_webhook.ts itself:
 *   1. verifyResendEventSignature() -- pure crypto, no D1/HTTP involved.
 *      Signs with the real HMAC-SHA256-over-`{id}.{timestamp}.{body}`
 *      scheme Svix documents, so these are genuine round-trip tests, not
 *      a mocked verifier.
 *   2. POST /email/events -- the route, using workerFetch() to override
 *      env.RESEND_WEBHOOK_SECRET per-test (same pattern billing.spec.ts
 *      uses for STRIPE_WEBHOOK_SECRET), signing real request bodies with
 *      the matching secret.
 */
import { env } from "cloudflare:test";
import { describe, expect, it } from "vitest";
import * as store from "../src/store";
import { verifyResendEventSignature } from "../src/resend_webhook";

function testExecutionContext(): ExecutionContext {
  return {
    waitUntil() {},
    passThroughOnException() {},
    props: {},
  } as unknown as ExecutionContext;
}

async function workerFetch(request: Request, envOverrides: Record<string, unknown> = {}): Promise<Response> {
  const worker = (await import("../src/index")).default;
  return worker.fetch(request, { ...env, ...envOverrides } as never, testExecutionContext());
}

function toBase64(bytes: Uint8Array): string {
  let bin = "";
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin);
}

function fakeSecret(): { raw: string; whsec: string } {
  const bytes = crypto.getRandomValues(new Uint8Array(32));
  const raw = toBase64(bytes);
  return { raw, whsec: `whsec_${raw}` };
}

async function signPayload(secretRaw: string, svixId: string, svixTimestamp: string, body: string): Promise<string> {
  const keyBytes = Uint8Array.from(atob(secretRaw), (c) => c.charCodeAt(0));
  const key = await crypto.subtle.importKey("raw", keyBytes as BufferSource, { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const signedContent = `${svixId}.${svixTimestamp}.${body}`;
  const mac = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(signedContent) as BufferSource);
  return `v1,${toBase64(new Uint8Array(mac))}`;
}

// ---------------------------------------------------------------------------
// verifyResendEventSignature() -- pure crypto
// ---------------------------------------------------------------------------

describe("verifyResendEventSignature", () => {
  it("verifies a genuine HMAC signature", async () => {
    const { raw, whsec } = fakeSecret();
    const svixId = "msg_1";
    const svixTimestamp = "1723190400";
    const body = JSON.stringify({ type: "email.bounced", data: { to: ["a@example.com"] } });
    const sig = await signPayload(raw, svixId, svixTimestamp, body);

    expect(await verifyResendEventSignature(whsec, svixId, svixTimestamp, sig, body, 300, 1723190400)).toBe(true);
  });

  it("holds across many random deliveries", async () => {
    const { raw, whsec } = fakeSecret();
    for (let i = 0; i < 15; i++) {
      const svixId = `msg_${i}`;
      const svixTimestamp = String(1700000000 + i);
      const body = JSON.stringify({ type: "email.delivered", data: { to: [`x${i}@example.com`] } });
      const sig = await signPayload(raw, svixId, svixTimestamp, body);
      expect(await verifyResendEventSignature(whsec, svixId, svixTimestamp, sig, body, 300, 1700000000 + i)).toBe(true);
    }
  });

  it("rejects a tampered body", async () => {
    const { raw, whsec } = fakeSecret();
    const svixId = "msg_1";
    const svixTimestamp = "1723190400";
    const body = JSON.stringify({ type: "email.bounced", data: { to: ["a@example.com"] } });
    const sig = await signPayload(raw, svixId, svixTimestamp, body);

    const tampered = body.replace("bounced", "delivered");
    expect(await verifyResendEventSignature(whsec, svixId, svixTimestamp, sig, tampered, 300, 1723190400)).toBe(false);
  });

  it("rejects a tampered svix-id (the id is part of the signed content, not incidental)", async () => {
    const { raw, whsec } = fakeSecret();
    const svixTimestamp = "1723190400";
    const body = JSON.stringify({ type: "email.bounced", data: { to: ["a@example.com"] } });
    const sig = await signPayload(raw, "msg_1", svixTimestamp, body);

    expect(await verifyResendEventSignature(whsec, "msg_2", svixTimestamp, sig, body, 300, 1723190400)).toBe(false);
  });

  it("rejects a signature from a different secret", async () => {
    const { raw } = fakeSecret();
    const { whsec: otherWhsec } = fakeSecret();
    const svixId = "msg_1";
    const svixTimestamp = "1723190400";
    const body = JSON.stringify({ type: "email.bounced", data: { to: ["a@example.com"] } });
    const sig = await signPayload(raw, svixId, svixTimestamp, body);

    expect(await verifyResendEventSignature(otherWhsec, svixId, svixTimestamp, sig, body, 300, 1723190400)).toBe(false);
  });

  it("rejects a delivery outside the timestamp tolerance window (replay protection)", async () => {
    const { raw, whsec } = fakeSecret();
    const svixId = "msg_1";
    const svixTimestamp = "1723190400";
    const body = JSON.stringify({ type: "email.bounced", data: { to: ["a@example.com"] } });
    const sig = await signPayload(raw, svixId, svixTimestamp, body);

    // "now" is 10 minutes after the signed timestamp -- outside the 5-minute default window.
    expect(await verifyResendEventSignature(whsec, svixId, svixTimestamp, sig, body, 300, 1723190400 + 600)).toBe(false);
    // Just inside the window still verifies.
    expect(await verifyResendEventSignature(whsec, svixId, svixTimestamp, sig, body, 300, 1723190400 + 299)).toBe(true);
  });

  it("verifies against any one of several space-separated v1 tokens (secret rotation)", async () => {
    const { raw, whsec } = fakeSecret();
    const svixId = "msg_1";
    const svixTimestamp = "1723190400";
    const body = JSON.stringify({ type: "email.bounced", data: { to: ["a@example.com"] } });
    const realSig = await signPayload(raw, svixId, svixTimestamp, body);
    const combined = `v1,bm90LXJlYWw= ${realSig} v1,YWxzby1ub3QtcmVhbA==`;

    expect(await verifyResendEventSignature(whsec, svixId, svixTimestamp, combined, body, 300, 1723190400)).toBe(true);
  });

  it("fails closed on malformed inputs -- never throws", async () => {
    const { whsec } = fakeSecret();
    expect(await verifyResendEventSignature(whsec, null, "123", "v1,AAAA", "{}")).toBe(false);
    expect(await verifyResendEventSignature(whsec, "msg_1", null, "v1,AAAA", "{}")).toBe(false);
    expect(await verifyResendEventSignature(whsec, "msg_1", "123", null, "{}")).toBe(false);
    expect(await verifyResendEventSignature(whsec, "msg_1", "not-a-number", "v1,AAAA", "{}")).toBe(false);
    expect(await verifyResendEventSignature("not-valid-base64!!", "msg_1", "123", "v1,AAAA", "{}")).toBe(false);
    expect(await verifyResendEventSignature(whsec, "msg_1", "123", "garbage-no-comma", "{}")).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// POST /email/events
// ---------------------------------------------------------------------------

async function postEvent(
  secret: { raw: string; whsec: string } | null,
  event: unknown,
  envOverrides: Record<string, unknown>,
  opts: { svixId?: string; timestampOverride?: string } = {}
): Promise<Response> {
  const body = JSON.stringify(event);
  const headers: Record<string, string> = { "content-type": "application/json" };
  if (secret) {
    const svixId = opts.svixId ?? `msg_${Date.now()}_${Math.random()}`;
    const timestamp = opts.timestampOverride ?? String(Math.floor(Date.now() / 1000));
    const sig = await signPayload(secret.raw, svixId, timestamp, body);
    headers["svix-id"] = svixId;
    headers["svix-timestamp"] = timestamp;
    headers["svix-signature"] = sig;
  }
  return workerFetch(
    new Request("https://deadline-radar.com/email/events", { method: "POST", headers, body }),
    envOverrides
  );
}

async function seedConfirmedSubscriber(email: string): Promise<void> {
  await store.addPending(env.DB, {
    email,
    stateSlug: "ohio",
    deadlineFields: {},
    deadlineSource: store.DEADLINE_SOURCE_USER,
    userDeadline: "2030-01-01",
    firstName: null,
    firmId: null,
    skipConfirmation: true,
  });
}

describe("POST /email/events", () => {
  it("503s when RESEND_WEBHOOK_SECRET is unset -- same unconfigured-rejects posture as /stripe/webhook", async () => {
    const resp = await postEvent(null, { type: "email.bounced", data: { to: ["a@example.com"] } }, {
      RESEND_WEBHOOK_SECRET: undefined,
    });
    expect(resp.status).toBe(503);
  });

  it("400s on a missing/invalid signature and mutates nothing", async () => {
    const secret = fakeSecret();
    const email = `resendevt-badsig-${Date.now()}@example.com`;
    await seedConfirmedSubscriber(email);

    const resp = await postEvent(null, { type: "email.bounced", data: { to: [email] } }, {
      RESEND_WEBHOOK_SECRET: secret.whsec,
    });
    expect(resp.status).toBe(400);
    expect(await store.isPermanentlySuppressed(env.DB, email)).toBe(false);
  });

  it("an email.bounced event suppresses every row sharing that email", async () => {
    const secret = fakeSecret();
    const email = `resendevt-bounce-${Date.now()}@example.com`;
    await seedConfirmedSubscriber(email);
    await store.addPending(env.DB, {
      email,
      stateSlug: "texas",
      deadlineFields: {},
      deadlineSource: store.DEADLINE_SOURCE_USER,
      userDeadline: "2030-02-01",
      firstName: null,
      firmId: null,
      skipConfirmation: true,
    });

    const resp = await postEvent(secret, { type: "email.bounced", data: { to: [email] } }, {
      RESEND_WEBHOOK_SECRET: secret.whsec,
    });
    expect(resp.status).toBe(200);
    expect(await store.isPermanentlySuppressed(env.DB, email)).toBe(true);
    for (const row of await store.listSubscriberLicenses(env.DB, email)) {
      expect(row.status).toBe(store.STATUS_STOPPED);
      expect(row.stop_reason).toBe("hard_bounced");
    }
  });

  it("an email.complained event suppresses too", async () => {
    const secret = fakeSecret();
    const email = `resendevt-spam-${Date.now()}@example.com`;
    await seedConfirmedSubscriber(email);

    const resp = await postEvent(secret, { type: "email.complained", data: { to: [email] } }, {
      RESEND_WEBHOOK_SECRET: secret.whsec,
    });
    expect(resp.status).toBe(200);
    expect(await store.isPermanentlySuppressed(env.DB, email)).toBe(true);
    const row = await store.listSubscriberLicenses(env.DB, email);
    expect(row[0]?.stop_reason).toBe("spam_complaint");
  });

  it("an email.delivery_delayed event (transient) is logged but does NOT suppress", async () => {
    const secret = fakeSecret();
    const email = `resendevt-delayed-${Date.now()}@example.com`;
    const svixId = `msg-delayed-${Date.now()}`;
    await seedConfirmedSubscriber(email);

    const resp = await postEvent(secret, { type: "email.delivery_delayed", data: { to: [email] } }, {
      RESEND_WEBHOOK_SECRET: secret.whsec,
    }, { svixId });
    expect(resp.status).toBe(200);
    expect(await store.isPermanentlySuppressed(env.DB, email)).toBe(false);
    const row = await env.DB.prepare("SELECT * FROM email_deliverability_events WHERE sg_event_id = ?1").bind(svixId).first();
    expect(row).toBeTruthy();
    expect((row as { event_type: string }).event_type).toBe("email.delivery_delayed");
  });

  it("a redelivered event (same svix-id) doesn't double-log or re-suppress destructively", async () => {
    const secret = fakeSecret();
    const email = `resendevt-redeliver-${Date.now()}@example.com`;
    const svixId = `msg-redeliver-${Date.now()}`;
    await seedConfirmedSubscriber(email);
    const overrides = { RESEND_WEBHOOK_SECRET: secret.whsec };

    const first = await postEvent(secret, { type: "email.bounced", data: { to: [email] } }, overrides, { svixId });
    expect(first.status).toBe(200);
    const second = await postEvent(secret, { type: "email.bounced", data: { to: [email] } }, overrides, { svixId });
    expect(second.status).toBe(200);

    const { results } = await env.DB.prepare("SELECT * FROM email_deliverability_events WHERE sg_event_id = ?1").bind(svixId).all();
    expect(results?.length).toBe(1);
  });

  it("a delivery with no usable `to` is accepted (200, signature was valid) but suppresses nothing", async () => {
    const secret = fakeSecret();
    const resp = await postEvent(secret, { type: "email.bounced", data: {} }, {
      RESEND_WEBHOOK_SECRET: secret.whsec,
    });
    expect(resp.status).toBe(200);
  });

  it("accepts `data.to` as a bare string, not just an array", async () => {
    const secret = fakeSecret();
    const email = `resendevt-stringto-${Date.now()}@example.com`;
    await seedConfirmedSubscriber(email);

    const resp = await postEvent(secret, { type: "email.bounced", data: { to: email } }, {
      RESEND_WEBHOOK_SECRET: secret.whsec,
    });
    expect(resp.status).toBe(200);
    expect(await store.isPermanentlySuppressed(env.DB, email)).toBe(true);
  });
});
