import { describe, it, expect } from "vitest";
import { checkPaidFeatureAccess, hasActiveTrial, hasValueLineAccess, isPreCutoverSignup, paidFeatureDenialMessage, VALUE_LINE_CUTOVER_DATE } from "../src/entitlements";

// AuditLab TIER-1 (LOW, 2026-08-29): isPreCutoverSignup() used to compare
// createdAt against VALUE_LINE_CUTOVER_DATE as STRINGS. The constant has no
// milliseconds; every real created_at (from store.ts's nowIso()) always
// does, and "." sorts before "Z" -- so a firm created anywhere in the one
// second 03:05:00.000Z-03:05:00.999Z on the cutover date compared as
// lexicographically before the cutover even though it happened at or after
// it, silently grandfathering a post-cutover signup. Reproduces AuditLab's
// exact table against the real function.
describe("isPreCutoverSignup() -- AuditLab TIER-1", () => {
  it("a whole second before the cutover is pre-cutover, with or without milliseconds", () => {
    expect(isPreCutoverSignup("2026-08-10T03:04:59Z")).toBe(true);
    expect(isPreCutoverSignup("2026-08-10T03:04:59.999Z")).toBe(true);
  });

  it("exactly the cutover instant (bare or with .000 milliseconds) is NOT pre-cutover", () => {
    expect(isPreCutoverSignup(VALUE_LINE_CUTOVER_DATE)).toBe(false);
    expect(isPreCutoverSignup("2026-08-10T03:05:00.000Z")).toBe(false);
  });

  it("any millisecond value during the cutover second is NOT pre-cutover -- the exact TIER-1 bug", () => {
    expect(isPreCutoverSignup("2026-08-10T03:05:00.123Z")).toBe(false);
    expect(isPreCutoverSignup("2026-08-10T03:05:00.999Z")).toBe(false);
  });

  it("a whole second after the cutover is not pre-cutover", () => {
    expect(isPreCutoverSignup("2026-08-10T03:05:01Z")).toBe(false);
  });
});

// SecurityLab ENT-1 (LOW, 2026-09-26): isPreCutoverSignup() used to fail
// CLOSED on malformed input only because Date.parse() returns NaN and every
// NaN comparison is false -- undocumented, and a semantically-"equivalent"
// rewrite (`!(Date.parse(c) >= Date.parse(CUTOVER))`) was fail-OPEN, granting
// grandfathered paid-tier access to any firm with an empty/null/garbage
// created_at. All 7 tests above pass under BOTH forms, so they could not
// catch an inverting refactor. Fixed with `Number.isFinite()` (explicit,
// refactor-proof: an inverted rewrite of THIS form still denies) plus a
// PRE_CUTOVER_FLOOR_DATE (2020-01-01, before this product existed) that also
// denies parseable-but-spurious dates (epoch, "0", a pre-2020 backdated
// import) which the bare NaN check would have granted.
describe("isPreCutoverSignup() -- malformed/spurious input, SecurityLab ENT-1", () => {
  it("unparseable created_at denies pre-cutover access (fails closed)", () => {
    expect(isPreCutoverSignup("")).toBe(false);
    expect(isPreCutoverSignup("garbage")).toBe(false);
  });

  it("parseable-but-spurious pre-2020 dates now deny too -- the two-sided fix", () => {
    expect(isPreCutoverSignup("1970-01-01T00:00:00Z")).toBe(false);
    expect(isPreCutoverSignup("0")).toBe(false);
    expect(isPreCutoverSignup("2019-12-31T23:59:59Z")).toBe(false);
  });

  it("a real pre-cutover instant on or after the floor still grants", () => {
    expect(isPreCutoverSignup("2020-01-01T00:00:00Z")).toBe(true);
  });
});

function firm(over: Partial<{ plan_tier: string; status: string }> = {}) {
  return {
    plan_tier: "free",
    status: "active",
    ...over,
  };
}

describe("paid-feature access (Map / Practice Privilege Check) -- fails closed", () => {
  it("denies an unrecognised plan tier rather than defaulting open", () => {
    // A typo in a tier name must lock the feature, not unlock it.
    for (const tier of ["", "Firm", "FIRM", "enterprise", "pilot", "trial", "premuim"]) {
      const res = checkPaidFeatureAccess(firm({ plan_tier: tier }));
      expect(res.allowed, `tier "${tier}" must not grant access`).toBe(false);
    }
  });

  it("the FREE tier (the renamed pilot) does NOT unlock paid features, no exception", () => {
    const res = checkPaidFeatureAccess(firm({ plan_tier: "free" }));
    expect(res.allowed).toBe(false);
    if (!res.allowed) expect(res.reason).toBe("tier_not_paid");
  });

  it("denies an inactive firm EVEN ON A PAID TIER", () => {
    // A suspended account with a paid tier must not retain access.
    const res = checkPaidFeatureAccess(firm({ plan_tier: "firm", status: "suspended" }));
    expect(res.allowed).toBe(false);
    if (!res.allowed) expect(res.reason).toBe("firm_inactive");
  });
});

describe("paid tiers", () => {
  it("allows every recognised paid tier", () => {
    for (const tier of [
      "firm",
      "firm_annual",
      "premium",
      // 2026-08-05, Stripe-backed paid tiers -- see tiers.ts. All carry the
      // identical PAID feature set; only the seat cap (checked separately,
      // in tiers.spec.ts) differs between them.
      "firm_starter",
      "firm_growth",
      "firm_standard",
      "firm_scale",
    ]) {
      const res = checkPaidFeatureAccess(firm({ plan_tier: tier }));
      expect(res.allowed, `tier "${tier}" should grant access`).toBe(true);
    }
  });

  it("individual is no longer a recognised paid tier -- folded into free 2026-08-09 (see worker.spec.ts for the solo-free exception, which lives in index.ts's gate wrapper, not here)", () => {
    const res = checkPaidFeatureAccess(firm({ plan_tier: "individual" }));
    expect(res.allowed).toBe(false);
  });

  it("a structurally FirmRow-shaped-but-not-literal row still satisfies checkPaidFeatureAccess (the parameter type is structural, not FirmRow-specific)", () => {
    // No `id`/`admin_email`/password fields -- proves the parameter type
    // genuinely only needs plan_tier/status, not a real FirmRow.
    const minimalSubject = { plan_tier: "firm", status: "active" };
    expect(checkPaidFeatureAccess(minimalSubject).allowed).toBe(true);
  });

  it("a paid tier is not time-bounded -- there is no expiration to check at all anymore", () => {
    expect(checkPaidFeatureAccess(firm({ plan_tier: "firm" })).allowed).toBe(true);
  });
});

// SecurityLab TRIAL-1 (MEDIUM, 2026-10-02): hasValueLineAccess()'s trial OR
// (and, pre-existing, its grandfather OR) used to apply with no `status`
// check -- a requestFirmDeletion()'d firm ('deleted') kept getting
// Slack/Teams/document sends for the rest of an active trial window, or
// forever if pre-cutover-grandfathered, defeating that function's own
// documented purpose. Each "should deny" case below is a positive control:
// it fails against the pre-fix `access.allowed || isPreCutoverSignup(...)
// || hasActiveTrial(...)` shape (that shape ignores status entirely once a
// trial or grandfather condition is true), and only passes once the
// `access.reason === "tier_not_paid"` status gate wraps both exceptions.
function entitlementFirm(over: Partial<{ plan_tier: string; status: string; created_at: string; trial_ends_at: string | null }> = {}) {
  return {
    plan_tier: "free",
    status: "active",
    created_at: "2026-09-01T00:00:00Z", // well after VALUE_LINE_CUTOVER_DATE -- not grandfathered
    trial_ends_at: null as string | null,
    ...over,
  };
}
const FUTURE_TRIAL = "2099-01-01T00:00:00Z";
const PAST_TRIAL = "2020-01-01T00:00:00Z";

describe("hasValueLineAccess() -- SecurityLab TRIAL-1", () => {
  it("a deleted firm on an active trial is denied, not granted", () => {
    const firm = entitlementFirm({ status: "deleted", trial_ends_at: FUTURE_TRIAL });
    expect(hasActiveTrial(firm.trial_ends_at)).toBe(true); // control: the trial itself is genuinely live
    expect(hasValueLineAccess(firm)).toBe(false);
  });

  it("a deleted, pre-cutover-grandfathered firm is denied too -- the same status gate closes both exceptions", () => {
    const firm = entitlementFirm({ status: "deleted", created_at: "2026-01-01T00:00:00Z" });
    expect(isPreCutoverSignup(firm.created_at)).toBe(true); // control: genuinely pre-cutover
    expect(hasValueLineAccess(firm)).toBe(false);
  });

  it("a suspended firm on an active trial is denied (firm_inactive, not just the deleted case)", () => {
    const firm = entitlementFirm({ status: "suspended", trial_ends_at: FUTURE_TRIAL });
    expect(hasValueLineAccess(firm)).toBe(false);
  });

  it("owner control: an ACTIVE firm on an active trial still gets access -- the fix must not deny everything", () => {
    const firm = entitlementFirm({ status: "active", trial_ends_at: FUTURE_TRIAL });
    expect(hasValueLineAccess(firm)).toBe(true);
  });

  it("owner control: an active, pre-cutover-grandfathered firm still gets access", () => {
    const firm = entitlementFirm({ status: "active", created_at: "2026-01-01T00:00:00Z" });
    expect(hasValueLineAccess(firm)).toBe(true);
  });

  it("owner control: an active firm on a real paid tier gets access regardless of trial/grandfather state", () => {
    const firm = entitlementFirm({ status: "active", plan_tier: "firm_starter", trial_ends_at: PAST_TRIAL });
    expect(hasValueLineAccess(firm)).toBe(true);
  });

  it("a deleted firm with an expired trial is denied (control: both the trial-expiry path and the status gate deny it)", () => {
    const firm = entitlementFirm({ status: "deleted", trial_ends_at: PAST_TRIAL });
    expect(hasValueLineAccess(firm)).toBe(false);
  });
});

describe("denial messaging", () => {
  it("does not tell a suspended account to pay us", () => {
    expect(paidFeatureDenialMessage("firm_inactive")).not.toMatch(/plan|pay|upgrade/i);
  });

  it("points a free-tier firm at picking a plan -- feature-agnostic wording (shared by Map and Practice Privilege Check, must not name either specifically)", () => {
    expect(paidFeatureDenialMessage("tier_not_paid")).toMatch(/paid firm plan/i);
    expect(paidFeatureDenialMessage("tier_not_paid")).not.toMatch(/mobility|map/i);
  });
});
