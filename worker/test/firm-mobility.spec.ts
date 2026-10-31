import { describe, it, expect } from "vitest";
import {
  evaluateFirmMobility,
  normalizeFirmRuleRow,
  isFirmRuleStale,
  firmRuleTtlDays,
  type FirmMobilityInput,
  type FirmMobilityRuleRow,
  type FirmMobilityCondition,
} from "../src/firm_mobility";

function condition(over: Partial<FirmMobilityCondition> = {}): FirmMobilityCondition {
  return {
    exists: true,
    citation: "Tex. Occ. Code s 901.462",
    citationUrl: "https://example.gov/statute",
    notes: null,
    ...over,
  };
}

/** A fully-verified rule where attest work by a no-office firm is
 * unconditionally exempt -- the ONLY shape that should ever produce a bare
 * "clear" verdict with no peer-review requirement attached. */
function verifiedRule(over: Partial<FirmMobilityRuleRow> = {}): FirmMobilityRuleRow {
  return {
    state: "Texas",
    stateSlug: "texas",
    attestExemption: condition({ exists: true }),
    physicalOfficeTrigger: condition({ exists: true, citation: "Tex. Occ. Code s 901.461" }),
    peerReviewConditionsPermit: condition({ exists: false, citation: null }),
    confidence: "dual_source",
    sourceDisagreement: null,
    sourceDisagreementScope: "all",
    verifiedDate: new Date().toISOString().slice(0, 10),
    ...over,
  };
}

function input(over: Partial<FirmMobilityInput> = {}): FirmMobilityInput {
  return {
    firmHomeStateSlug: "california",
    targetStateSlug: "texas",
    hasPhysicalOfficeInTargetState: false,
    ...over,
  };
}

describe("SAFETY: the engine must never assert a clearance it cannot cite", () => {
  it("downgrades a would-be CLEAR verdict to not_verified when the attest exemption has NO citation", () => {
    const uncited = verifiedRule({ attestExemption: condition({ exists: true, citation: null }) });
    const finding = evaluateFirmMobility(input(), uncited, null);
    expect(finding.verdict).toBe("not_verified");
    expect(finding.citation).toBeNull();
    expect(finding.confidence).toBeNull();
  });

  it("does NOT downgrade action_required or not_verified for a missing citation -- only clear requires one", () => {
    const notExempt = verifiedRule({ attestExemption: condition({ exists: false, citation: null }) });
    const finding = evaluateFirmMobility(input(), notExempt, null);
    expect(finding.verdict).toBe("action_required");
  });
});

describe("no physical office -- the no-office attest-work case this dataset targets", () => {
  it("clear, unconditional: attest_exemption true, no peer-review condition", () => {
    const rule = verifiedRule();
    const finding = evaluateFirmMobility(input(), rule, null);
    expect(finding.verdict).toBe("clear");
    expect(finding.requirements).toEqual([]);
  });

  it("action_required: attest_exemption false -- must register regardless of peer review", () => {
    const rule = verifiedRule({ attestExemption: condition({ exists: false }) });
    const finding = evaluateFirmMobility(input(), rule, null);
    expect(finding.verdict).toBe("action_required");
  });

  it("the 4 genuinely-unclear states surface as not_verified, never guessed", () => {
    const rule = verifiedRule({ attestExemption: condition({ exists: null, citation: null }) });
    const finding = evaluateFirmMobility(input(), rule, null);
    expect(finding.verdict).toBe("not_verified");
  });
});

describe("the peer-review-conditional exemption -- the case this feature exists to get right", () => {
  function conditionalRule(): FirmMobilityRuleRow {
    return verifiedRule({
      attestExemption: condition({ exists: true, citation: "State Code s 1" }),
      peerReviewConditionsPermit: condition({
        exists: true,
        citation: "State Code s 2",
        notes: "Exemption requires current peer review.",
      }),
    });
  }

  it("stays clear when the firm's own peer-review due date is set and in the future", () => {
    const future = new Date(Date.now() + 90 * 86_400_000).toISOString().slice(0, 10);
    const finding = evaluateFirmMobility(input(), conditionalRule(), future);
    expect(finding.verdict).toBe("clear");
    expect(finding.requirements.length).toBeGreaterThan(0); // condition still visible, not hidden
    expect(finding.requirements.join(" ")).toContain("peer review");
  });

  it("downgrades to action_required when no peer-review due date is on file", () => {
    const finding = evaluateFirmMobility(input(), conditionalRule(), null);
    expect(finding.verdict).toBe("action_required");
    expect(finding.requirements.join(" ")).toMatch(/no peer-review due date on file/i);
  });

  it("downgrades to action_required when the on-file peer-review due date is in the PAST", () => {
    const past = new Date(Date.now() - 30 * 86_400_000).toISOString().slice(0, 10);
    const finding = evaluateFirmMobility(input(), conditionalRule(), past);
    expect(finding.verdict).toBe("action_required");
    expect(finding.requirements.join(" ")).toContain(past);
  });

  it("the peer-review condition is never silently folded away -- always its own requirements[] entry", () => {
    // This is the exact case the plan's own "31 vs 18 states" mismatch
    // surfaced: a naive boolean-AND enum would hide this distinction.
    // Verify the requirement text survives even on the CLEAR path.
    const future = new Date(Date.now() + 90 * 86_400_000).toISOString().slice(0, 10);
    const finding = evaluateFirmMobility(input(), conditionalRule(), future);
    expect(finding.requirements).toContain("Exemption requires current peer review.");
  });

  it("null peerReviewConditionsPermit.exists (unverified whether it's conditional) forces not_verified", () => {
    const rule = verifiedRule({
      attestExemption: condition({ exists: true }),
      peerReviewConditionsPermit: condition({ exists: null, citation: null }),
    });
    const finding = evaluateFirmMobility(input(), rule, null);
    expect(finding.verdict).toBe("not_verified");
  });
});

describe("physical office in the target state -- a separate, unconditional trigger", () => {
  it("action_required when physical_office_trigger is true, REGARDLESS of attest exemption", () => {
    const rule = verifiedRule({
      attestExemption: condition({ exists: true }), // would be clear/exempt for the no-office case
      physicalOfficeTrigger: condition({ exists: true }),
    });
    const finding = evaluateFirmMobility(input({ hasPhysicalOfficeInTargetState: true }), rule, null);
    expect(finding.verdict).toBe("action_required");
  });

  it("SRC-145: physical_office_trigger false is NEVER clear -- action_required carrying the row's own trigger notes", () => {
    const notes = "Registration required only if the office performs attest work or uses the CPA title.";
    const rule = verifiedRule({ physicalOfficeTrigger: condition({ exists: false, notes }) });
    const finding = evaluateFirmMobility(input({ hasPhysicalOfficeInTargetState: true }), rule, null);
    expect(finding.verdict).toBe("action_required");
    expect(finding.requirements).toEqual([notes]);
    expect(finding.summary).not.toMatch(/haven't identified/);
    expect(finding.citation).toBe("Tex. Occ. Code s 901.462");
  });

  it("SRC-145: physical_office_trigger false with no notes is not_verified (we cannot state the trigger), never clear", () => {
    const rule = verifiedRule({ physicalOfficeTrigger: condition({ exists: false, notes: null }) });
    const finding = evaluateFirmMobility(input({ hasPhysicalOfficeInTargetState: true }), rule, null);
    expect(finding.verdict).toBe("not_verified");
  });

  it("not_verified when physical_office_trigger is unverified", () => {
    const rule = verifiedRule({ physicalOfficeTrigger: condition({ exists: null, citation: null }) });
    const finding = evaluateFirmMobility(input({ hasPhysicalOfficeInTargetState: true }), rule, null);
    expect(finding.verdict).toBe("not_verified");
  });

  it("does NOT consult attestExemption at all when a physical office is present", () => {
    // attestExemption.exists is null (would force not_verified on the
    // no-office path) but physicalOfficeTrigger is a clean false -- the
    // physical-office branch must not be contaminated by an unrelated
    // condition it doesn't read.
    const rule = verifiedRule({
      attestExemption: condition({ exists: null, citation: null }),
      physicalOfficeTrigger: condition({ exists: false, notes: "Office plus attest work triggers registration." }),
    });
    const finding = evaluateFirmMobility(input({ hasPhysicalOfficeInTargetState: true }), rule, null);
    expect(finding.verdict).toBe("action_required");
    expect(finding.requirements).toEqual(["Office plus attest work triggers registration."]);
  });
});

describe("SAFETY: source disagreement and staleness block regardless of every other field", () => {
  it("a non-null sourceDisagreement forces not_verified even on an otherwise clean, cited, permissive rule", () => {
    const rule = verifiedRule({ sourceDisagreement: "Statute and board guidance conflict on this point." });
    const finding = evaluateFirmMobility(input(), rule, null);
    expect(finding.verdict).toBe("not_verified");
    expect(finding.requirements).toContain("Statute and board guidance conflict on this point.");
  });

  it("an unscoped ('all') disagreement blocks the has-office limb too", () => {
    const rule = verifiedRule({ sourceDisagreement: "Statute and rule conflict." });
    const finding = evaluateFirmMobility(input({ hasPhysicalOfficeInTargetState: true }), rule, null);
    expect(finding.verdict).toBe("not_verified");
    expect(finding.requirements).toContain("Statute and rule conflict.");
  });

  it("SRC-146: an attest-scoped disagreement blocks the no-office limb only; the undisputed office answer still renders", () => {
    const rule = verifiedRule({
      sourceDisagreement: "Attest-exemption question only: statute and rule conflict.",
      sourceDisagreementScope: "attest_exemption",
    });
    const noOffice = evaluateFirmMobility(input({ hasPhysicalOfficeInTargetState: false }), rule, null);
    expect(noOffice.verdict).toBe("not_verified");
    expect(noOffice.requirements).toContain("Attest-exemption question only: statute and rule conflict.");
    const office = evaluateFirmMobility(input({ hasPhysicalOfficeInTargetState: true }), rule, null);
    expect(office.verdict).toBe("action_required");
    expect(office.summary).not.toMatch(/disagree/);
  });

  it("SRC-146: an office-scoped disagreement blocks the office limb only", () => {
    const rule = verifiedRule({ sourceDisagreement: "Office question conflict.", sourceDisagreementScope: "physical_office_trigger" });
    expect(evaluateFirmMobility(input({ hasPhysicalOfficeInTargetState: true }), rule, null).verdict).toBe("not_verified");
    expect(evaluateFirmMobility(input({ hasPhysicalOfficeInTargetState: false }), rule, null).verdict).toBe("clear");
  });

  it("SRC-146: the normalizer defaults a missing or unrecognized scope to 'all' (blocks both limbs)", () => {
    const raw = { state: "Texas", state_slug: "texas", confidence: "dual_source", source_disagreement: "x conflicts y" };
    expect(normalizeFirmRuleRow(raw)?.sourceDisagreementScope).toBe("all");
    expect(normalizeFirmRuleRow({ ...raw, source_disagreement_scope: "attest" })?.sourceDisagreementScope).toBe("all");
    expect(normalizeFirmRuleRow({ ...raw, source_disagreement_scope: "attest_exemption" })?.sourceDisagreementScope).toBe(
      "attest_exemption"
    );
  });

  it("a rule verified more than the TTL window ago is not_verified regardless of its booleans", () => {
    const stale = verifiedRule({ verifiedDate: new Date(Date.now() - 200 * 86_400_000).toISOString().slice(0, 10) });
    expect(isFirmRuleStale(stale)).toBe(true);
    const finding = evaluateFirmMobility(input(), stale, null);
    expect(finding.verdict).toBe("not_verified");
  });

  it("a rule with no verified_date at all is treated as stale, not as freshly unknown-but-fine", () => {
    const noDate = verifiedRule({ verifiedDate: null });
    expect(isFirmRuleStale(noDate)).toBe(true);
  });
});

describe("GUARD-23: thin-source firm rows expire on the 90-day bar", () => {
  const daysAgo = (n: number) => new Date(Date.now() - n * 86_400_000).toISOString().slice(0, 10);

  it("positive control: a dual_source row with no disagreement keeps the 180-day bar at 100 days", () => {
    const rule = verifiedRule({ confidence: "dual_source", sourceDisagreement: null, verifiedDate: daysAgo(100) });
    expect(firmRuleTtlDays(rule)).toBe(180);
    expect(isFirmRuleStale(rule)).toBe(false);
    expect(evaluateFirmMobility(input(), rule, null).verdict).not.toBe("not_verified");
  });

  it("a single_source row is stale at 100 days and the copy names the 90-day bar", () => {
    const rule = verifiedRule({ confidence: "single_source", verifiedDate: daysAgo(100) });
    expect(firmRuleTtlDays(rule)).toBe(90);
    expect(isFirmRuleStale(rule)).toBe(true);
    const finding = evaluateFirmMobility(input(), rule, null);
    expect(finding.verdict).toBe("not_verified");
    expect(finding.requirements.join(" ")).toContain("every 90 days");
  });

  it("a single_source row at 80 days is not stale (the bar is 90, not lower)", () => {
    expect(isFirmRuleStale(verifiedRule({ confidence: "single_source", verifiedDate: daysAgo(80) }))).toBe(false);
  });

  it("a non-empty prose sourceDisagreement takes the 90-day bar even on a dual_source row (no boolean field exists)", () => {
    const rule = verifiedRule({ confidence: "dual_source", sourceDisagreement: "Statute and rule conflict.", verifiedDate: daysAgo(100) });
    expect(firmRuleTtlDays(rule)).toBe(90);
    expect(isFirmRuleStale(rule)).toBe(true);
    expect(firmRuleTtlDays(verifiedRule({ confidence: "dual_source", sourceDisagreement: null }))).toBe(180);
  });

  it("the normalizer maps an empty-string source_disagreement to the 180-day bar, a real one to 90", () => {
    const raw = { state: "Texas", state_slug: "texas", confidence: "dual_source", verified_date: daysAgo(100) };
    const empty = normalizeFirmRuleRow({ ...raw, source_disagreement: "" });
    const real = normalizeFirmRuleRow({ ...raw, source_disagreement: "Board FAQ contradicts the statute." });
    expect(empty && firmRuleTtlDays(empty)).toBe(180);
    expect(real && firmRuleTtlDays(real)).toBe(90);
  });
});

describe("home == target and missing rule", () => {
  it("home state equals target -> not_applicable, not a lookup on the rule at all", () => {
    const finding = evaluateFirmMobility(input({ firmHomeStateSlug: "texas", targetStateSlug: "texas" }), null, null);
    expect(finding.verdict).toBe("not_applicable");
  });

  it("no rule for the target state -> not_verified, never guessed", () => {
    const finding = evaluateFirmMobility(input(), null, null);
    expect(finding.verdict).toBe("not_verified");
  });
});

describe("REGRESSION: undefined-is-not-null defect (same class mobility.ts's own review caught)", () => {
  it("an OMITTED exists key on a condition must never pass through as permissive", () => {
    const row = normalizeFirmRuleRow({
      state: "Texas",
      state_slug: "texas",
      attest_exemption: {}, // exists key omitted entirely -> undefined, not null
      physical_office_trigger: { exists: true, citation: "cite", citation_url: "https://x.gov" },
      peer_review_conditions_permit: {},
      confidence: "dual_source",
      verified_date: new Date().toISOString().slice(0, 10),
    });
    expect(row?.attestExemption.exists).toBeNull(); // coerced to null, never true
    const finding = evaluateFirmMobility(input(), row, null);
    expect(finding.verdict).toBe("not_verified");
  });
});

describe("REGRESSION: placeholder citations must not satisfy the citation-required guard", () => {
  it("a 'TBD' citation on the attest exemption is treated as no citation at all", () => {
    const row = normalizeFirmRuleRow({
      state: "Texas",
      state_slug: "texas",
      attest_exemption: { exists: true, citation: "TBD", citation_url: null },
      physical_office_trigger: { exists: true, citation: "real cite", citation_url: "https://x.gov" },
      peer_review_conditions_permit: { exists: false },
      confidence: "dual_source",
      verified_date: new Date().toISOString().slice(0, 10),
    });
    expect(row?.attestExemption.citation).toBeNull();
    const finding = evaluateFirmMobility(input(), row, null);
    expect(finding.verdict).toBe("not_verified");
  });
});

describe("disclaimer is always present", () => {
  it("every verdict shape carries the disclaimer", () => {
    for (const finding of [
      evaluateFirmMobility(input({ firmHomeStateSlug: "texas", targetStateSlug: "texas" }), null, null),
      evaluateFirmMobility(input(), null, null),
      evaluateFirmMobility(input(), verifiedRule(), null),
      evaluateFirmMobility(input(), verifiedRule({ attestExemption: condition({ exists: false }) }), null),
    ]) {
      expect(finding.disclaimer).toContain("not legal advice");
    }
  });
});
