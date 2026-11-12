// SRC-239(c): California firm permit (ca-firm), Bus. & Prof. Code 5070.5(b)
// plus the CBA firm licensure page's odd/even year rule.
import { env, SELF } from "cloudflare:test";
import { describe, expect, it } from "vitest";
import { computeSubscriberDeadline } from "../src/deadline";
import type { SubscriberRow } from "../src/store";

function postSubscribe(fields: Record<string, string>, ip: string): Promise<Response> {
  return SELF.fetch("https://deadline-radar.com/subscribe", {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded", "cf-connecting-ip": ip },
    body: new URLSearchParams({ hp_website: "", ...fields }).toString(),
  });
}

const iso = (d: Date | null) => (d ? d.toISOString().slice(0, 10) : null);

describe("ca-firm computation", () => {
  it("expires on the last day of the approval month, in years matching the approval year's parity", () => {
    const asOf = new Date("2026-11-12T00:00:00Z");
    // Approved March 2025 (odd): next is March 2027.
    expect(iso(computeSubscriberDeadline("california", { license_type_id: "ca-firm", permit_month: "3", permit_year: "2025" }, asOf))).toBe("2027-03-31");
    // Approved December 2010 (even): December 2026 is still ahead.
    expect(iso(computeSubscriberDeadline("california", { license_type_id: "ca-firm", permit_month: "12", permit_year: "2010" }, asOf))).toBe("2026-12-31");
    // Approved February 2024 (even, leap): Feb 2026 has passed, so Feb 29 2028.
    expect(iso(computeSubscriberDeadline("california", { license_type_id: "ca-firm", permit_month: "2", permit_year: "2024" }, asOf))).toBe("2028-02-29");
  });

  it("never returns the approval year itself: a permit approved this month first expires two years out", () => {
    const asOf = new Date("2026-11-12T00:00:00Z");
    expect(iso(computeSubscriberDeadline("california", { license_type_id: "ca-firm", permit_month: "11", permit_year: "2026" }, asOf))).toBe("2028-11-30");
    expect(iso(computeSubscriberDeadline("california", { license_type_id: "ca-firm", permit_month: "1", permit_year: "2026" }, asOf))).toBe("2028-01-31");
  });

  it("returns null on missing or bad fields instead of falling back to the individual rule", () => {
    const asOf = new Date("2026-11-12T00:00:00Z");
    expect(computeSubscriberDeadline("california", { license_type_id: "ca-firm" }, asOf)).toBeNull();
    expect(computeSubscriberDeadline("california", { license_type_id: "ca-firm", birth_month: "3", birth_year_parity: "odd" }, asOf)).toBeNull();
    expect(computeSubscriberDeadline("california", { license_type_id: "ca-firm", permit_month: "13", permit_year: "2020" }, asOf)).toBeNull();
  });

  it("leaves the individual rule unchanged", () => {
    const asOf = new Date("2026-11-12T00:00:00Z");
    expect(iso(computeSubscriberDeadline("california", { birth_month: "5", birth_year_parity: "odd" }, asOf))).toBe("2027-05-31");
  });
});

describe("ca-firm signup", () => {
  it("persists license_type_id, approval month and year", async () => {
    const email = `ca-firm-${Date.now()}@example.com`;
    const resp = await postSubscribe({ email, state: "california", license_type_id: "ca-firm", permit_month: "3", permit_year: "2025" }, "203.0.113.141");
    expect(resp.status).toBe(200);
    const row = await env.DB.prepare("SELECT * FROM subscribers WHERE email = ?1").bind(email).first<SubscriberRow>();
    expect(JSON.parse(row?.deadline_fields ?? "{}")).toEqual({ license_type_id: "ca-firm", permit_month: "3", permit_year: "2025" });
    expect(row?.deadline_source).toBe("computed");
  });

  it("refuses a firm permit with no approval month/year, and a future approval month", async () => {
    const none = await postSubscribe({ email: `ca-firm-none-${Date.now()}@example.com`, state: "california", license_type_id: "ca-firm", birth_month: "3", birth_year: "1980" }, "203.0.113.142");
    expect(none.status).toBe(400);
    expect(await none.text()).toContain("month and year the permit was originally approved");
    const future = await postSubscribe({ email: `ca-firm-future-${Date.now()}@example.com`, state: "california", license_type_id: "ca-firm", permit_month: "1", permit_year: "2099" }, "203.0.113.143");
    expect(future.status).toBe(400);
    expect(await future.text()).toContain("be in the future");
  });

  it("refuses an unknown California license type, and keeps the individual path for ca-individual or no type", async () => {
    const bad = await postSubscribe({ email: `ca-bad-${Date.now()}@example.com`, state: "california", license_type_id: "az-firm", birth_month: "3", birth_year: "1980" }, "203.0.113.144");
    expect(bad.status).toBe(400);
    for (const [ip, extra] of [["203.0.113.145", { license_type_id: "ca-individual" }], ["203.0.113.146", {}]] as const) {
      const email = `ca-ind-${ip}-${Date.now()}@example.com`;
      const resp = await postSubscribe({ email, state: "california", birth_month: "5", birth_year: "1991", ...extra }, ip);
      expect(resp.status).toBe(200);
      const row = await env.DB.prepare("SELECT * FROM subscribers WHERE email = ?1").bind(email).first<SubscriberRow>();
      expect(JSON.parse(row?.deadline_fields ?? "{}")).toEqual({ birth_month: "5", birth_year_parity: "odd" });
    }
  });
});
