// SRC-239(c): Texas firm office license (tx-firm), 22 TAC 515.3(c)(1):
// fees due the last day of a board-assigned renewal month, every year.
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

describe("tx-firm computation", () => {
  it("is due the last day of the assigned renewal month, every year", () => {
    const asOf = new Date("2026-11-12T00:00:00Z");
    expect(iso(computeSubscriberDeadline("texas", { license_type_id: "tx-firm", renewal_month: "11" }, asOf))).toBe("2026-11-30");
    expect(iso(computeSubscriberDeadline("texas", { license_type_id: "tx-firm", renewal_month: "3" }, asOf))).toBe("2027-03-31");
    expect(iso(computeSubscriberDeadline("texas", { license_type_id: "tx-firm", renewal_month: "2" }, new Date("2027-12-01T00:00:00Z")))).toBe("2028-02-29");
  });

  it("returns null on a missing or bad month instead of falling back to the birth month", () => {
    const asOf = new Date("2026-11-12T00:00:00Z");
    expect(computeSubscriberDeadline("texas", { license_type_id: "tx-firm" }, asOf)).toBeNull();
    expect(computeSubscriberDeadline("texas", { license_type_id: "tx-firm", birth_month: "5" }, asOf)).toBeNull();
    expect(computeSubscriberDeadline("texas", { license_type_id: "tx-firm", renewal_month: "13" }, asOf)).toBeNull();
  });

  it("leaves the individual rule unchanged", () => {
    const asOf = new Date("2026-11-12T00:00:00Z");
    expect(iso(computeSubscriberDeadline("texas", { birth_month: "5" }, asOf))).toBe("2027-05-31");
  });
});

describe("tx-firm signup", () => {
  it("persists license_type_id and the assigned renewal month", async () => {
    const email = `tx-firm-${Date.now()}@example.com`;
    const resp = await postSubscribe({ email, state: "texas", license_type_id: "tx-firm", renewal_month: "4" }, "203.0.113.151");
    expect(resp.status).toBe(200);
    const row = await env.DB.prepare("SELECT * FROM subscribers WHERE email = ?1").bind(email).first<SubscriberRow>();
    expect(JSON.parse(row?.deadline_fields ?? "{}")).toEqual({ license_type_id: "tx-firm", renewal_month: "4" });
    expect(row?.deadline_source).toBe("computed");
  });

  it("refuses a firm office license with no renewal month", async () => {
    const none = await postSubscribe({ email: `tx-firm-none-${Date.now()}@example.com`, state: "texas", license_type_id: "tx-firm", birth_month: "3" }, "203.0.113.152");
    expect(none.status).toBe(400);
    expect(await none.text()).toContain("renewal month the Board assigned your firm");
  });

  it("refuses an unknown Texas license type, and keeps the individual path for tx-individual or no type", async () => {
    const bad = await postSubscribe({ email: `tx-bad-${Date.now()}@example.com`, state: "texas", license_type_id: "ca-firm", birth_month: "3" }, "203.0.113.153");
    expect(bad.status).toBe(400);
    for (const [ip, extra] of [["203.0.113.154", { license_type_id: "tx-individual" }], ["203.0.113.155", {}]] as const) {
      const email = `tx-ind-${ip}-${Date.now()}@example.com`;
      const resp = await postSubscribe({ email, state: "texas", birth_month: "5", ...extra }, ip);
      expect(resp.status).toBe(200);
      const row = await env.DB.prepare("SELECT * FROM subscribers WHERE email = ?1").bind(email).first<SubscriberRow>();
      expect(JSON.parse(row?.deadline_fields ?? "{}")).toEqual({ birth_month: "5" });
    }
  });
});
