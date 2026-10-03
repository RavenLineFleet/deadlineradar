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

const RESEND_URL = "https://api.resend.com/emails";
const STRIPE_PRICE_URL = (id: string) => `https://api.stripe.com/v1/prices/${id}`;

function stripePriceResponse(
  overrides: Partial<{ unit_amount: number; currency: string; interval: string; interval_count: number | null; usage_type: string | null; active: boolean }> = {}
) {
  return {
    id: "price_test",
    unit_amount: overrides.unit_amount ?? 19900,
    currency: overrides.currency ?? "usd",
    // BILL-23/23b (SecurityLab, confirmed by AuditLab, 2026-10-03): every
    // fixture defaults to the correct shape (interval_count=1, licensed)
    // so the existing "all match" tests stay all-matching; the new
    // BILL-22/23 tests below override exactly one field at a time.
    recurring: { interval: overrides.interval ?? "year", interval_count: overrides.interval_count ?? 1, usage_type: overrides.usage_type ?? "licensed" },
    active: overrides.active ?? true,
  };
}

const BASE_ENV = {
  STRIPE_SECRET_KEY: "sk_test_x",
  RESEND_API_KEY: "test-key",
  STRIPE_PRICE_FIRM_STARTER: "price_starter",
  STRIPE_PRICE_FIRM_GROWTH: "price_growth",
  STRIPE_PRICE_FIRM_STANDARD: "price_standard",
  STRIPE_PRICE_FIRM_SCALE: "price_scale",
};

// PR6-A (AuditLab, 2026-10-02): the 6 prices PR6 added (4 monthly tiers +
// 2 per-seat) -- a separate const so the pre-existing tests above (which
// deliberately leave these unset to prove "unconfigured = skipped, not a
// mismatch") are unaffected.
const PR6_ENV = {
  STRIPE_PRICE_FIRM_STARTER_MONTHLY: "price_starter_monthly",
  STRIPE_PRICE_FIRM_GROWTH_MONTHLY: "price_growth_monthly",
  STRIPE_PRICE_FIRM_STANDARD_MONTHLY: "price_standard_monthly",
  STRIPE_PRICE_FIRM_SCALE_MONTHLY: "price_scale_monthly",
  STRIPE_PRICE_PER_SEAT_ADDON_ANNUAL: "price_addon_annual",
  STRIPE_PRICE_PER_SEAT_ADDON_MONTHLY: "price_addon_monthly",
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
      if (url === RESEND_URL) {
        const body = JSON.parse(String(init?.body)) as { subject: string; text: string };
        captured.push({ subject: body.subject, text: body.text ?? "" });
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
      if (url === RESEND_URL) {
        const body = JSON.parse(String(init?.body)) as { text: string };
        captured.push(body.text ?? "");
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

  // BILL-22 (SecurityLab, MEDIUM, confirmed by AuditLab, 2026-10-03): an
  // archived Price (amount/interval/currency all still correct) refuses
  // every checkout for that tier -- the nightly cron already checked
  // `active` before this fix (unlike the script), but had no test pinning
  // it; this is that test (AuditLab's own TEST-14).
  it("BILL-22/TEST-14: active=false is flagged even when amount/interval/currency are all correct", async () => {
    const captured: string[] = [];
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = typeof input === "string" ? input : (input as Request).url;
      if (url === STRIPE_PRICE_URL("price_starter")) return Response.json(stripePriceResponse({ active: false }));
      if (url === STRIPE_PRICE_URL("price_growth")) return Response.json(stripePriceResponse({ unit_amount: 29900 }));
      if (url === STRIPE_PRICE_URL("price_standard")) return Response.json(stripePriceResponse({ unit_amount: 39900 }));
      if (url === STRIPE_PRICE_URL("price_scale")) return Response.json(stripePriceResponse({ unit_amount: 54900 }));
      if (url === RESEND_URL) {
        const body = JSON.parse(String(init?.body)) as { text: string };
        captured.push(body.text ?? "");
        return new Response(null, { status: 202 });
      }
      throw new Error(`unexpected fetch: ${url}`);
    });
    try {
      await freshRun({ SEND_APPROVED_PASSES: "stripePriceParityAlert" });
      expect(captured).toHaveLength(1);
      expect(captured[0]).toContain("active=false -- Stripe would refuse a checkout using this price entirely");
    } finally {
      fetchSpy.mockRestore();
    }
  });

  // BILL-23 (SecurityLab, MEDIUM, confirmed by AuditLab, 2026-10-03): an
  // interval_count=3 "month" price bills quarterly, not monthly -- every
  // other field (amount/interval/currency/active) stays correct.
  it("BILL-23: recurring.interval_count=3 on a monthly price is flagged (bills quarterly, not monthly)", async () => {
    const captured: string[] = [];
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = typeof input === "string" ? input : (input as Request).url;
      if (url === STRIPE_PRICE_URL("price_starter")) return Response.json(stripePriceResponse({ interval_count: 3 }));
      if (url === STRIPE_PRICE_URL("price_growth")) return Response.json(stripePriceResponse({ unit_amount: 29900 }));
      if (url === STRIPE_PRICE_URL("price_standard")) return Response.json(stripePriceResponse({ unit_amount: 39900 }));
      if (url === STRIPE_PRICE_URL("price_scale")) return Response.json(stripePriceResponse({ unit_amount: 54900 }));
      if (url === RESEND_URL) {
        const body = JSON.parse(String(init?.body)) as { text: string };
        captured.push(body.text ?? "");
        return new Response(null, { status: 202 });
      }
      throw new Error(`unexpected fetch: ${url}`);
    });
    try {
      await freshRun({ SEND_APPROVED_PASSES: "stripePriceParityAlert" });
      expect(captured).toHaveLength(1);
      expect(captured[0]).toContain("recurring.interval_count=3 (expected 1)");
    } finally {
      fetchSpy.mockRestore();
    }
  });

  // BILL-23b (SecurityLab, confirmed by AuditLab, 2026-10-03): a metered
  // Price doesn't accept a line-item quantity the way a licensed one
  // does -- checked on every price uniformly since nothing here bills
  // metered, but the per-seat add-ons are the only ones sent WITH a
  // quantity, so this is the one that would actually bite.
  it("BILL-23b: recurring.usage_type=metered on the per-seat add-on is flagged", async () => {
    const captured: string[] = [];
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = typeof input === "string" ? input : (input as Request).url;
      if (url === STRIPE_PRICE_URL("price_starter")) return Response.json(stripePriceResponse({ unit_amount: 19900 }));
      if (url === STRIPE_PRICE_URL("price_growth")) return Response.json(stripePriceResponse({ unit_amount: 29900 }));
      if (url === STRIPE_PRICE_URL("price_standard")) return Response.json(stripePriceResponse({ unit_amount: 39900 }));
      if (url === STRIPE_PRICE_URL("price_scale")) return Response.json(stripePriceResponse({ unit_amount: 54900 }));
      if (url === STRIPE_PRICE_URL("price_starter_monthly")) return Response.json(stripePriceResponse({ unit_amount: 2000, interval: "month" }));
      if (url === STRIPE_PRICE_URL("price_growth_monthly")) return Response.json(stripePriceResponse({ unit_amount: 2900, interval: "month" }));
      if (url === STRIPE_PRICE_URL("price_standard_monthly")) return Response.json(stripePriceResponse({ unit_amount: 3900, interval: "month" }));
      if (url === STRIPE_PRICE_URL("price_scale_monthly")) return Response.json(stripePriceResponse({ unit_amount: 5500, interval: "month" }));
      if (url === STRIPE_PRICE_URL("price_addon_annual")) return Response.json(stripePriceResponse({ unit_amount: 1500, usage_type: "metered" }));
      if (url === STRIPE_PRICE_URL("price_addon_monthly")) return Response.json(stripePriceResponse({ unit_amount: 150, interval: "month" }));
      if (url === RESEND_URL) {
        const body = JSON.parse(String(init?.body)) as { text: string };
        captured.push(body.text ?? "");
        return new Response(null, { status: 202 });
      }
      throw new Error(`unexpected fetch: ${url}`);
    });
    try {
      await freshRun({ SEND_APPROVED_PASSES: "stripePriceParityAlert", ...PR6_ENV });
      expect(captured).toHaveLength(1);
      expect(captured[0]).toContain("STRIPE_PRICE_PER_SEAT_ADDON_ANNUAL");
      expect(captured[0]).toContain('recurring.usage_type=metered (expected "licensed")');
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
      if (url === RESEND_URL) {
        const body = JSON.parse(String(init?.body)) as { text: string };
        captured.push(body.text ?? "");
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
      if (url === RESEND_URL) {
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
      await expect(freshRun({ SEND_APPROVED_PASSES: "stripePriceParityAlert", RESEND_API_KEY: undefined })).resolves.toBeUndefined();
      // Month was never claimed -- a later tick with SendGrid configured can still alert.
      expect(await store.claimStripePriceParityAlertForMonth(env.DB, month)).toBe(true);
    } finally {
      fetchSpy.mockRestore();
    }
  });

  it("PR6-A: all 10 prices (4 annual + 4 monthly + 2 per-seat) configured and matching -- 10 fetches, no send", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = typeof input === "string" ? input : (input as Request).url;
      const amounts: Record<string, { amount: number; interval: string }> = {
        price_starter: { amount: 19900, interval: "year" },
        price_growth: { amount: 29900, interval: "year" },
        price_standard: { amount: 39900, interval: "year" },
        price_scale: { amount: 54900, interval: "year" },
        price_starter_monthly: { amount: 2000, interval: "month" },
        price_growth_monthly: { amount: 2900, interval: "month" },
        price_standard_monthly: { amount: 3900, interval: "month" },
        price_scale_monthly: { amount: 5500, interval: "month" },
        price_addon_annual: { amount: 1500, interval: "year" },
        price_addon_monthly: { amount: 150, interval: "month" },
      };
      for (const [id, v] of Object.entries(amounts)) {
        if (url === STRIPE_PRICE_URL(id)) return Response.json(stripePriceResponse({ unit_amount: v.amount, interval: v.interval }));
      }
      throw new Error(`unexpected fetch: ${url}`);
    });
    try {
      await freshRun({ SEND_APPROVED_PASSES: "stripePriceParityAlert", ...PR6_ENV });
      expect(fetchSpy).toHaveBeenCalledTimes(10);
    } finally {
      fetchSpy.mockRestore();
    }
  });

  it("PR6-A: a MONTHLY price pointed at the annual amount is caught -- the exact failure PR6-A named (copy-pasting the wrong Price id)", async () => {
    const captured: string[] = [];
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = typeof input === "string" ? input : (input as Request).url;
      if (url === STRIPE_PRICE_URL("price_starter")) return Response.json(stripePriceResponse({ unit_amount: 19900 }));
      if (url === STRIPE_PRICE_URL("price_growth")) return Response.json(stripePriceResponse({ unit_amount: 29900 }));
      if (url === STRIPE_PRICE_URL("price_standard")) return Response.json(stripePriceResponse({ unit_amount: 39900 }));
      if (url === STRIPE_PRICE_URL("price_scale")) return Response.json(stripePriceResponse({ unit_amount: 54900 }));
      // Essentials-monthly Price id was accidentally pointed at the $199/yr annual Price.
      if (url === STRIPE_PRICE_URL("price_starter_monthly")) return Response.json(stripePriceResponse({ unit_amount: 19900, interval: "year" }));
      if (url === STRIPE_PRICE_URL("price_growth_monthly")) return Response.json(stripePriceResponse({ unit_amount: 2900, interval: "month" }));
      if (url === STRIPE_PRICE_URL("price_standard_monthly")) return Response.json(stripePriceResponse({ unit_amount: 3900, interval: "month" }));
      if (url === STRIPE_PRICE_URL("price_scale_monthly")) return Response.json(stripePriceResponse({ unit_amount: 5500, interval: "month" }));
      if (url === STRIPE_PRICE_URL("price_addon_annual")) return Response.json(stripePriceResponse({ unit_amount: 1500 }));
      if (url === STRIPE_PRICE_URL("price_addon_monthly")) return Response.json(stripePriceResponse({ unit_amount: 150, interval: "month" }));
      if (url === RESEND_URL) {
        const body = JSON.parse(String(init?.body)) as { text: string };
        captured.push(body.text ?? "");
        return new Response(null, { status: 202 });
      }
      throw new Error(`unexpected fetch: ${url}`);
    });
    try {
      await freshRun({ SEND_APPROVED_PASSES: "stripePriceParityAlert", ...PR6_ENV });
      expect(captured).toHaveLength(1);
      expect(captured[0]).toContain("STRIPE_PRICE_FIRM_STARTER_MONTHLY");
      expect(captured[0]).toContain("$20/mo"); // PR6-A's own emails.ts fix: not the old hardcoded "/yr"
      expect(captured[0]).toContain('unit_amount=19900 (expected 2000');
      expect(captured[0]).toContain('recurring.interval=year (expected "month")');
    } finally {
      fetchSpy.mockRestore();
    }
  });

  it("PR6-A: the per-seat add-on prices are checked too, independent of the tier prices", async () => {
    const captured: string[] = [];
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = typeof input === "string" ? input : (input as Request).url;
      if (url === STRIPE_PRICE_URL("price_starter")) return Response.json(stripePriceResponse({ unit_amount: 19900 }));
      if (url === STRIPE_PRICE_URL("price_growth")) return Response.json(stripePriceResponse({ unit_amount: 29900 }));
      if (url === STRIPE_PRICE_URL("price_standard")) return Response.json(stripePriceResponse({ unit_amount: 39900 }));
      if (url === STRIPE_PRICE_URL("price_scale")) return Response.json(stripePriceResponse({ unit_amount: 54900 }));
      if (url === STRIPE_PRICE_URL("price_starter_monthly")) return Response.json(stripePriceResponse({ unit_amount: 2000, interval: "month" }));
      if (url === STRIPE_PRICE_URL("price_growth_monthly")) return Response.json(stripePriceResponse({ unit_amount: 2900, interval: "month" }));
      if (url === STRIPE_PRICE_URL("price_standard_monthly")) return Response.json(stripePriceResponse({ unit_amount: 3900, interval: "month" }));
      if (url === STRIPE_PRICE_URL("price_scale_monthly")) return Response.json(stripePriceResponse({ unit_amount: 5500, interval: "month" }));
      if (url === STRIPE_PRICE_URL("price_addon_annual")) return Response.json(stripePriceResponse({ unit_amount: 999 })); // mismatch: expected 1500
      if (url === STRIPE_PRICE_URL("price_addon_monthly")) return Response.json(stripePriceResponse({ unit_amount: 150, interval: "month" }));
      if (url === RESEND_URL) {
        const body = JSON.parse(String(init?.body)) as { text: string };
        captured.push(body.text ?? "");
        return new Response(null, { status: 202 });
      }
      throw new Error(`unexpected fetch: ${url}`);
    });
    try {
      await freshRun({ SEND_APPROVED_PASSES: "stripePriceParityAlert", ...PR6_ENV });
      expect(captured).toHaveLength(1);
      expect(captured[0]).toContain("STRIPE_PRICE_PER_SEAT_ADDON_ANNUAL");
      expect(captured[0]).toContain("unit_amount=999 (expected 1500, i.e. $15)");
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
      if (url === RESEND_URL) return new Response("simulated failure", { status: 500 });
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
