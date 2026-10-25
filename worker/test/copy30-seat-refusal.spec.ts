/**
 * AuditLab COPY-30 (2026-10-25): the paid-tier seat-cap 402 said "there's no
 * self-serve tier change yet" after self-serve plan change went live
 * 2026-10-11. Each arm of seatRefusalResponse() pinned here.
 */
import { env } from "cloudflare:test";
import { describe, expect, it } from "vitest";
import { seatRefusalResponse } from "../src/index";

type Body = { error: string; pay_now_url: string };
const on = { ...env, SELF_SERVE_PLAN_CHANGE: "on" } as typeof env;
const off = { ...env, SELF_SERVE_PLAN_CHANGE: "" } as typeof env;

async function refuse(e: typeof env, cap: number, planTier: string): Promise<Body> {
  const r = seatRefusalResponse(e, "seat_cap", cap, planTier);
  expect(r.status).toBe(402);
  return r.json<Body>();
}

describe("COPY-30 seat-cap 402", () => {
  for (const [tier, cap] of [["firm_starter", 5], ["firm_growth", 10], ["firm_standard", 20]] as const) {
    it(`${tier} with self-serve on -> Manage billing, dashboard Account tab`, async () => {
      const b = await refuse(on, cap, tier);
      expect(b.error).toContain(`up to ${cap} staff`);
      expect(b.error).toContain("Manage billing");
      expect(b.error).not.toContain("no self-serve");
      expect(b.pay_now_url).toBe("/firm-dashboard/#account");
    });
  }

  it("firm_scale (top tier) -> contact, no larger plan claimed", async () => {
    const b = await refuse(on, 35, "firm_scale");
    expect(b.error).toContain("our largest plan");
    expect(b.error).not.toContain("Manage billing");
    expect(b.pay_now_url).toBe("/contact/");
  });

  it("paid tier with self-serve off -> contact", async () => {
    const b = await refuse(off, 5, "firm_starter");
    expect(b.error).toContain("Email us");
    expect(b.error).not.toContain("Manage billing");
    expect(b.pay_now_url).toBe("/contact/");
  });

  it("free/pilot -> upgrade prompt to the Account tab either way", async () => {
    for (const e of [on, off]) {
      const b = await refuse(e, 3, "free");
      expect(b.error).toBe("Your plan covers up to 3 staff. Upgrade to add more.");
      expect(b.pay_now_url).toBe("/firm-dashboard/#account");
    }
  });

  it("no arm still says there's no self-serve tier change", async () => {
    for (const e of [on, off]) for (const t of ["free", "firm_starter", "firm_growth", "firm_standard", "firm_scale"]) {
      expect((await refuse(e, 5, t)).error).not.toMatch(/no self-serve tier change/i);
    }
  });
});
