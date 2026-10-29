import { describe, it, expect } from "vitest";
import rulesData from "../src/mobility_rules.json";
import {
  normalizeRuleRow,
  evaluateIndividualMobility,
  evaluateFirmRegistration,
  type MobilityInput,
  type MobilityRuleRow,
} from "../src/mobility";

/**
 * REG-6 (AuditLab, 2026-10-12): D.C.'s mobility rule rests on emergency legislation that expires 2026-11-08. The
 * sunset used to exist only in prose, so nothing could fire on it. These pin the date-typed field's behavior:
 * a caveat on every answer until the date, and a fail-closed not_verified from the date until re-verified.
 *
 * GUARD-22 (2026-10-29): D.C. Law 26-183 made that rule permanent effective 2026-09-25, so the shipped record no
 * longer carries a sunset. The mechanism tests below keep the real D.C. row but inject the old 2026-11-08 sunset.
 */
function dc(over: Record<string, unknown> = {}): MobilityRuleRow {
  const raw = (rulesData as { records: Record<string, unknown>[] }).records.find((r) => r.state_slug === "dc");
  const row = normalizeRuleRow({ ...raw, rule_expires_on: "2026-11-08", ...over });
  if (!row) throw new Error("dc row did not normalize");
  return row;
}

const input = (over: Partial<MobilityInput> = {}): MobilityInput => ({
  homeStateSlug: "texas",
  targetStateSlug: "dc",
  serviceType: "tax",
  licenseInGoodStanding: true,
  substantiallyEquivalent: true,
  ...over,
});

const day = (iso: string) => new Date(iso + "T12:00:00Z");

describe("REG-6: rule_expires_on", () => {
  it("is on no shipped record now that D.C.'s rule is permanent (D.C. Law 26-183, eff. 2026-09-25)", () => {
    const withField = (rulesData as { records: Record<string, unknown>[] }).records.filter((r) => r.rule_expires_on);
    expect(withField.map((r) => [r.state_slug, r.rule_expires_on])).toEqual([]);
    expect(dc().rule_expires_on).toBe("2026-11-08");
  });

  it("before the sunset: still answers, and the answer says the rule is time-limited", () => {
    const res = evaluateIndividualMobility(input(), dc({ verified_date: "2026-10-12" }), null, day("2026-10-12"));
    expect(res.verdict).not.toBe("not_verified");
    expect(res.summary).toContain("expires on 2026-11-08");
    expect(res.requirements.join(" ")).toContain("time-limited legislation that expires on 2026-11-08");
  });

  it("the firm path carries the same caveat", () => {
    const res = evaluateFirmRegistration(input({ serviceType: "attest" }), dc({ verified_date: "2026-10-12" }), day("2026-10-12"));
    expect(res.verdict).not.toBe("not_verified");
    expect(res.summary).toContain("expires on 2026-11-08");
  });

  it("on the sunset date with no re-verification: not_verified, for both paths", () => {
    for (const d of ["2026-11-08", "2026-11-09", "2026-11-20"]) {
      const row = dc({ verified_date: "2026-10-12" });
      expect(evaluateIndividualMobility(input(), row, null, day(d)).verdict).toBe("not_verified");
      expect(evaluateFirmRegistration(input({ serviceType: "attest" }), row, day(d)).verdict).toBe("not_verified");
    }
    const res = evaluateIndividualMobility(input(), dc({ verified_date: "2026-10-12" }), null, day("2026-11-09"));
    expect(res.summary).toContain("time-limited legislation whose end date has arrived");
    expect(res.requirements.join(" ")).toContain("2026-11-08");
  });

  it("the boundary is the start of the sunset day (UTC): one second before is answered, the instant itself is blocked", () => {
    const row = dc({ verified_date: "2026-10-12" });
    expect(evaluateIndividualMobility(input(), row, null, new Date("2026-11-07T23:59:59Z")).verdict).not.toBe("not_verified");
    expect(evaluateIndividualMobility(input(), row, null, new Date("2026-11-08T00:00:00Z")).verdict).toBe("not_verified");
  });

  it("re-verifying on or after the sunset date unblocks it, and drops the stale caveat", () => {
    const row = dc({ verified_date: "2026-11-09" });
    const res = evaluateIndividualMobility(input(), row, null, day("2026-11-10"));
    expect(res.verdict).not.toBe("not_verified");
    expect(res.summary).not.toContain("expires on 2026-11-08");
  });

  it("a malformed or unparseable date fails CLOSED, never silently ignored", () => {
    const res = evaluateIndividualMobility(input(), dc({ verified_date: "2026-10-12", rule_expires_on: "sometime in November" }), null, day("2026-10-12"));
    expect(res.verdict).toBe("not_verified");
  });

  it("a row with no rule_expires_on behaves exactly as before (no caveat, no block)", () => {
    const row = dc({ verified_date: "2026-10-12", rule_expires_on: null });
    const res = evaluateIndividualMobility(input(), row, null, day("2026-12-01"));
    expect(res.summary).not.toContain("time-limited");
  });
});
