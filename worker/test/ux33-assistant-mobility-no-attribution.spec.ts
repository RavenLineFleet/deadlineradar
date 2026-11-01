// UX-33: /assistant/mobility never collects an attestation, so no payload may
// attribute a statement to the caller ("You've indicated ..."), and the
// top-level disclaimer must not claim standard licensure was assumed.
import { describe, expect, it } from "vitest";
import { evaluateMobility, normalizeRuleRow, type MobilityRuleRow } from "../src/mobility";
import { buildAssistantMobilityResponse } from "../src/assistant";
import rules from "../src/mobility_rules.json";

const SERVICE_TYPES = ["attest", "tax", "other_non_attest"] as const;
const bySlug: Record<string, MobilityRuleRow> = {};
for (const raw of ((rules as any).records ?? []) as unknown[]) {
  const row = normalizeRuleRow(raw);
  if (row) bySlug[row.state_slug] = row;
}
const slugs = Object.keys(bySlug);

describe("UX-33 assistant mobility copy", () => {
  it("has a real population", () => {
    expect(slugs.length).toBeGreaterThan(40);
  });
  it("never says the caller indicated anything, across every pair and service type", () => {
    let checked = 0;
    for (const home of slugs) for (const target of slugs) {
      if (home === target) continue;
      for (const st of SERVICE_TYPES) {
        const result = evaluateMobility(
          { homeStateSlug: home, targetStateSlug: target, serviceType: st as any, licenseInGoodStanding: false, substantiallyEquivalent: false },
          bySlug[target] ?? null, undefined, bySlug[home] ?? null,
        );
        const body = JSON.stringify(buildAssistantMobilityResponse(home, target, st, result));
        expect(body).not.toMatch(/You've indicated/);
        expect(body).not.toMatch(/assumes standard licensure/);
        checked++;
      }
    }
    expect(checked).toBe(slugs.length * (slugs.length - 1) * SERVICE_TYPES.length);
  });
  it("positive control: the engine itself still uses the attested copy on the paid path", () => {
    const h = slugs[0]!, t = slugs[1]!;
    const r = evaluateMobility(
      { homeStateSlug: h, targetStateSlug: t, serviceType: "attest" as any, licenseInGoodStanding: false, substantiallyEquivalent: false },
      bySlug[t] ?? null, undefined, bySlug[h] ?? null,
    );
    expect(r.individual.summary).toMatch(/You've indicated/);
  });
});
