/**
 * AuditLab BILL-17 (MEDIUM, 2026-09-09): nothing previously read a Stripe
 * Price object back and compared it against what tiers.ts advertises --
 * an ordinary dashboard edit or a repointed STRIPE_PRICE_FIRM_* env var
 * would silently desync the advertised price from what a customer is
 * actually charged, with every existing gate still passing. This is the
 * runStripePriceParityAlertPass() coverage, same shape as
 * assistant-latency-alert.spec.ts's runAssistantLatencyAlertPass tests.
 */
import { env } from "cloudflare:test";
import { describe, expect, it, vi, beforeEach } from "vitest";
import * as store from "../src/store";

const SENDGRID_URL = "https://api.sendgrid.com/v3/mail/send";
const STRIPE_PRICE_URL = (id: string) => `https://api.stripe.com/v1/prices/${id}`;

function stripePriceResponse(overrides: Partial<{ unit_amount: number; currency: string; interval: string; active: boolean }> = {}) {
  return {
    id: "price_test",
    unit_amount: overrides.unit_amount ?? 19900,
    currency: overrides.currency ?? "usd",
    recurring: { interval: overrides.interval ?? "year" },
    active: overrides.active ?? true,
  };
}

const BASE_ENV = {
  STRIPE_SECRET_KEY: "sk_test_x",
  SENDGRID_API_KEY: "test-key",
  STRIPE_PRICE_FIRM_STARTER: "price_starter",
  STRIPE_PRICE_FIRM_GROWTH: "price_growth",
  STRIPE_PRICE_FIRM_STANDARD: "price_standard",
  STRIPE_PRICE_FIRM_SCALE: "price_scale",
};

describe("claimStripePriceParityAlertForMonth / unclaim -- month-keyed dedup", () => {
  it("first claim for a month succeeds, a second claim the same month fails", async () => {
    const month = "2099-01";
    expect(await store.claimStripePriceParityAlertForMonth(env.DB, month)).toBe(true);
    expect(await store.claimStripePriceParityAlertForMonth(env.DB, month)).toBe(false);
  });

  it("unclaim releases the month so a later attempt can claim it again", async () => {
    const month = "2099-02";
    expect(await store.claimStripePriceParityAlertForMonth(env.DB, month)).toBe(true);
    await store.unclaimStripePriceParityAlertForMonth(env.DB, month);
    expect(await store.claimStripePriceParityAlertForMonth(env.DB, month)).toBe(true);
  });
});

describe("runStripePriceParityAlertPass -- the gated, thresholded send", () => {
  beforeEach(async () => {
    await env.DB.prepare("DELETE FROM stripe_price_parity_alert_log").run();
  });

  async function freshRun(overrides: Record<string, unknown>) {
    const { runStripePriceParityAlertPass } = await import("../src/scheduler");
    return runStripePriceParityAlertPass({ ...env, ...BASE_ENV, ...overrides } as never);
  }

  it("does nothing (no fetch call at all) when SEND_APPROVED_PASSES doesn't include this pass -- fails closed by default", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    try {
      await freshRun({ SEND_APPROVED_PASSES: undefined });
      expect(fetchSpy).not.toHaveBeenCalled();
    } finally {
      fetchSpy.mockRestore();
    }
  });

  it("no STRIPE_SECRET_KEY -- degrades safely, no fetch attempted, no throw", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    try {
      await expect(freshRun({ SEND_APPROVED_PASSES: "stripePriceParityAlert", STRIPE_SECRET_KEY: undefined })).resolves.toBeUndefined();
      expect(fetchSpy).not.toHaveBeenCalled();
    } finally {
      fetchSpy.mockRestore();
    }
  });

  it("a tier with no configured price env var is silently skipped -- not a mismatch", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockImplementation(async () => {
      throw new Error("must not fetch a price for an unconfigured tier");
    });
    try {
      await freshRun({
        SEND_APPROVED_PASSES: "stripePriceParityAlert",
        STRIPE_PRICE_FIRM_STARTER: undefined,
        STRIPE_PRICE_FIRM_GROWTH: undefined,
        STRIPE_PRICE_FIRM_STANDARD: undefined,
        STRIPE_PRICE_FIRM_SCALE: undefined,
      });
      expect(fetchSpy).not.toHaveBeenCalled();
    } finally {
      fetchSpy.mockRestore();
    }
  });

  it("all 4 prices match tiers.ts exactly -- no send", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = typeof input === "string" ? input : (input as Request).url;
      if (url === STRIPE_PRICE_URL("price_starter")) return Response.json(stripePriceResponse({ unit_amount: 19900 }));
      if (url === STRIPE_PRICE_URL("price_growth")) return Response.json(stripePriceResponse({ unit_amount: 29900 }));
      if (url === STRIPE_PRICE_URL("price_standard")) return Response.json(stripePriceResponse({ unit_amount: 39900 }));
      if (url === STRIPE_PRICE_URL("price_scale")) return Response.json(stripePriceResponse({ unit_amount: 54900 }));
      throw new Error(`unexpected fetch: ${url}`);
    });
    try {
      await freshRun({ SEND_APPROVED_PASSES: "stripePriceParityAlert" });
      expect(fetchSpy).toHaveBeenCalledTimes(4); // 4 Stripe GETs, 0 SendGrid POST
    } finally {
      fetchSpy.mockRestore();
    }
  });

  it("one tier's unit_amount diverges from priceUsd -- sends an alert naming it", async () => {
    const captured: Array<{ subject: string; text: string }> = [];
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = typeof input === "string" ? input : (input as Request).url;
      if (url === STRIPE_PRICE_URL("price_starter")) return Response.json(stripePriceResponse({ unit_amount: 18800 })); // mismatch: $188 not $199
      if (url === STRIPE_PRICE_URL("price_growth")) return Response.json(stripePriceResponse({ unit_amount: 29900 }));
      if (url === STRIPE_PRICE_URL("price_standard")) return Response.json(stripePriceResponse({ unit_amount: 39900 }));
      if (url === STRIPE_PRICE_URL("price_scale")) return Response.json(stripePriceResponse({ unit_amount: 54900 }));
      if (url === SENDGRID_URL) {
        const body = JSON.parse(String(init?.body)) as { subject: string; content: Array<{ value: string }> };
        captured.push({ subject: body.subject, text: body.content[0]?.value ?? "" });
        return new Response(null, { status: 202 });
      }
      throw new Error(`unexpected fetch: ${url}`);
    });
    try {
      await freshRun({ SEND_APPROVED_PASSES: "stripePriceParityAlert" });
      expect(captured).toHaveLength(1);
      expect(captured[0]?.subject).toContain("1 Stripe price");
      expect(captured[0]?.text).toContain("STRIPE_PRICE_FIRM_STARTER");
      expect(captured[0]?.text).toContain("unit_amount=18800");
      expect(captured[0]?.text).toContain("expected 19900");
    } finally {
      fetchSpy.mockRestore();
    }
  });

  it("wrong currency and wrong interval are both flagged, independent of the amount", async () => {
    const captured: string[] = [];
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = typeof input === "string" ? input : (input as Request).url;
      if (url === STRIPE_PRICE_URL("price_starter")) return Response.json(stripePriceResponse({ currency: "eur" }));
      if (url === STRIPE_PRICE_URL("price_growth")) return Response.json(stripePriceResponse({ interval: "month" }));
      if (url === STRIPE_PRICE_URL("price_standard")) return Response.json(stripePriceResponse({ unit_amount: 39900 }));
      if (url === STRIPE_PRICE_URL("price_scale")) return Response.json(stripePriceResponse({ unit_amount: 54900 }));
      if (url === SENDGRID_URL) {
        const body = JSON.parse(String(init?.body)) as { content: Array<{ value: string }> };
        captured.push(body.content[0]?.value ?? "");
        return new Response(null, { status: 202 });
      }
      throw new Error(`unexpected fetch: ${url}`);
    });
    try {
      await freshRun({ SEND_APPROVED_PASSES: "stripePriceParityAlert" });
      expect(captured).toHaveLength(1);
      expect(captured[0]).toContain('currency=eur (expected "usd")');
      expect(captured[0]).toContain('recurring.interval=month (expected "year")');
    } finally {
      fetchSpy.mockRestore();
    }
  });

  it("a configured price id Stripe doesn't recognize (404) is reported as a mismatch, not silently skipped", async () => {
    const captured: string[] = [];
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = typeof input === "string" ? input : (input as Request).url;
      if (url === STRIPE_PRICE_URL("price_starter")) return new Response(JSON.stringify({ error: { message: "No such price" } }), { status: 404 });
      if (url === STRIPE_PRICE_URL("price_growth")) return Response.json(stripePriceResponse({ unit_amount: 29900 }));
      if (url === STRIPE_PRICE_URL("price_standard")) return Response.json(stripePriceResponse({ unit_amount: 39900 }));
      if (url === STRIPE_PRICE_URL("price_scale")) return Response.json(stripePriceResponse({ unit_amount: 54900 }));
      if (url === SENDGRID_URL) {
        const body = JSON.parse(String(init?.body)) as { content: Array<{ value: string }> };
        captured.push(body.content[0]?.value ?? "");
        return new Response(null, { status: 202 });
      }
      throw new Error(`unexpected fetch: ${url}`);
    });
    try {
      await freshRun({ SEND_APPROVED_PASSES: "stripePriceParityAlert" });
      expect(captured).toHaveLength(1);
      expect(captured[0]).toContain("not found or rejected by Stripe");
    } finally {
      fetchSpy.mockRestore();
    }
  });

  it("a breach sends at most once per UTC calendar month -- a second call the same month is a no-op", async () => {
    let sendCount = 0;
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = typeof input === "string" ? input : (input as Request).url;
      if (url === STRIPE_PRICE_URL("price_starter")) return Response.json(stripePriceResponse({ unit_amount: 1 })); // always mismatched
      if (url === STRIPE_PRICE_URL("price_growth")) return Response.json(stripePriceResponse({ unit_amount: 29900 }));
      if (url === STRIPE_PRICE_URL("price_standard")) return Response.json(stripePriceResponse({ unit_amount: 39900 }));
      if (url === STRIPE_PRICE_URL("price_scale")) return Response.json(stripePriceResponse({ unit_amount: 54900 }));
      if (url === SENDGRID_URL) {
        sendCount++;
        return new Response(null, { status: 202 });
      }
      throw new Error(`unexpected fetch: ${url}`);
    });
    try {
      await freshRun({ SEND_APPROVED_PASSES: "stripePriceParityAlert" });
      await freshRun({ SEND_APPROVED_PASSES: "stripePriceParityAlert" });
      expect(sendCount).toBe(1);
    } finally {
      fetchSpy.mockRestore();
    }
  });

  it("no SENDGRID_API_KEY with a real mismatch -- logs, does not throw, does not claim the month (so a later config fix can alert once SendGrid is available)", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = typeof input === "string" ? input : (input as Request).url;
      if (url === STRIPE_PRICE_URL("price_starter")) return Response.json(stripePriceResponse({ unit_amount: 1 }));
      if (url === STRIPE_PRICE_URL("price_growth")) return Response.json(stripePriceResponse({ unit_amount: 29900 }));
      if (url === STRIPE_PRICE_URL("price_standard")) return Response.json(stripePriceResponse({ unit_amount: 39900 }));
      if (url === STRIPE_PRICE_URL("price_scale")) return Response.json(stripePriceResponse({ unit_amount: 54900 }));
      throw new Error(`unexpected fetch: ${url}`);
    });
    try {
      const month = new Date().toISOString().slice(0, 7);
      await expect(freshRun({ SEND_APPROVED_PASSES: "stripePriceParityAlert", SENDGRID_API_KEY: undefined })).resolves.toBeUndefined();
      // Month was never claimed -- a later tick with SendGrid configured can still alert.
      expect(await store.claimStripePriceParityAlertForMonth(env.DB, month)).toBe(true);
    } finally {
      fetchSpy.mockRestore();
    }
  });

  it("a SendGrid failure (non-202) unclaims the month, so a later tick can retry rather than losing the alert silently", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = typeof input === "string" ? input : (input as Request).url;
      if (url === STRIPE_PRICE_URL("price_starter")) return Response.json(stripePriceResponse({ unit_amount: 1 }));
      if (url === STRIPE_PRICE_URL("price_growth")) return Response.json(stripePriceResponse({ unit_amount: 29900 }));
      if (url === STRIPE_PRICE_URL("price_standard")) return Response.json(stripePriceResponse({ unit_amount: 39900 }));
      if (url === STRIPE_PRICE_URL("price_scale")) return Response.json(stripePriceResponse({ unit_amount: 54900 }));
      if (url === SENDGRID_URL) return new Response("simulated failure", { status: 500 });
      throw new Error(`unexpected fetch: ${url}`);
    });
    try {
      const month = new Date().toISOString().slice(0, 7);
      await freshRun({ SEND_APPROVED_PASSES: "stripePriceParityAlert" });
      expect(await store.claimStripePriceParityAlertForMonth(env.DB, month)).toBe(true); // released, not burned
    } finally {
      fetchSpy.mockRestore();
    }
  });
});
