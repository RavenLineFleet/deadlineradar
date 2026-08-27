/**
 * Read-only lookup functions backing /api/assistant/* -- built for
 * orchestrator's DeadlineRadar chat assistant (droplet-side service) to call
 * live instead of keeping a second copy of this data (2026-08-27, see
 * AssetLab/inbox/orchestrator_20260827_1720_data_access_design.md for the
 * request). Every function here returns ONLY an explicit allowlist of
 * customer-safe fields, never a raw record spread -- the LEAK-2 lesson from
 * the same night: `verification_history` (every dataset) and
 * `computation.note` (cpa_deadlines.json specifically) are internal
 * maintainer audit trail, never customer copy, and a raw spread would ship
 * them the moment a new internal field is added without anyone touching
 * this file. `data_gap_note` is safe here (matches its established
 * convention across generate.py's own renderers for these same 4 datasets)
 * -- NOT true of mobility_rules.json's own data_gap_note, which is the
 * opposite convention (internal there); mobility/rule-changes below use
 * mobility.ts's own MobilityResult and reg_change_events.json's already-
 * public-by-design fields instead, not a data_gap_note read.
 *
 * Pure functions only -- no D1, no rate limiting, no HTTP. index.ts's route
 * handlers own the HTTP concerns (query parsing, jsonResponse, rate limit
 * buckets) and call into these, same separation this file's siblings
 * (mobility.ts holds evaluateMobility, index.ts holds the route wrapping
 * it) already use.
 */
import cpaData from "./cpa_deadlines.json";
import cpeHoursData from "./cpe_hours.json";
import reinstatementData from "./reinstatement.json";
import renewalFeesData from "./renewal_fees.json";
import regChangeEventsData from "./reg_change_events.json";
import { stateNameForSlug } from "./deadline";
import type { MobilityResult } from "./mobility";

interface AssistantDeadlineResult {
  state: string;
  state_slug: string;
  license_type: string;
  license_type_label: string;
  cycle_description: string;
  next_deadline_computed: string | null;
  data_gap_note: string | null;
  citation: string | null;
  citation_url: string | null;
  last_verified: string;
}

export function lookupAssistantDeadlines(stateSlug: string, licenseType?: string): AssistantDeadlineResult[] {
  const records = (cpaData.records as unknown[]).filter(
    (r): r is Record<string, unknown> => typeof r === "object" && r !== null
  );
  return records
    .filter((r) => r.state_slug === stateSlug && (!licenseType || r.license_type === licenseType))
    .map((r) => ({
      state: String(r.state ?? ""),
      state_slug: String(r.state_slug ?? ""),
      license_type: String(r.license_type ?? ""),
      license_type_label: String(r.license_type_label ?? ""),
      cycle_description: String(r.cycle_description ?? ""),
      next_deadline_computed: typeof r.next_deadline_computed === "string" ? r.next_deadline_computed : null,
      data_gap_note: typeof r.data_gap_note === "string" && r.data_gap_note ? r.data_gap_note : null,
      citation: typeof r.citation === "string" ? r.citation : null,
      citation_url: typeof r.citation_url === "string" ? r.citation_url : null,
      last_verified: String(r.last_verified ?? ""),
    }));
}

interface AssistantCpeResult {
  state: string;
  state_slug: string;
  total_hours: number | null;
  period_years: number | null;
  annual_minimum_hours: number | null;
  ethics_hours: number | null;
  ethics_period_years: number | null;
  notes: string | null;
  data_gap_note: string | null;
  citation: string | null;
  citation_url: string | null;
  verified_date: string;
}

export function lookupAssistantCpe(stateSlug: string): AssistantCpeResult | null {
  const records = cpeHoursData.records as Record<string, unknown>[];
  const r = records.find((rec) => rec.state_slug === stateSlug);
  if (!r) return null;
  return {
    state: String(r.state ?? ""),
    state_slug: String(r.state_slug ?? ""),
    total_hours: typeof r.total_hours === "number" ? r.total_hours : null,
    period_years: typeof r.period_years === "number" ? r.period_years : null,
    annual_minimum_hours: typeof r.annual_minimum_hours === "number" ? r.annual_minimum_hours : null,
    ethics_hours: typeof r.ethics_hours === "number" ? r.ethics_hours : null,
    ethics_period_years: typeof r.ethics_period_years === "number" ? r.ethics_period_years : null,
    notes: typeof r.notes === "string" && r.notes ? r.notes : null,
    data_gap_note: typeof r.data_gap_note === "string" && r.data_gap_note ? r.data_gap_note : null,
    citation: typeof r.citation === "string" ? r.citation : null,
    citation_url: typeof r.citation_url === "string" ? r.citation_url : null,
    verified_date: String(r.verified_date ?? ""),
  };
}

interface AssistantReinstatementResult {
  state: string;
  state_slug: string;
  reinstatement_fee_usd: number | null;
  reinstatement_fee_notes: string | null;
  penalty_cpe_hours: number | null;
  penalty_cpe_notes: string | null;
  penalty_ethics_hours: number | null;
  lapse_trigger: string | null;
  data_gap_note: string | null;
  citation: string | null;
  citation_url: string | null;
  last_verified: string;
}

export function lookupAssistantReinstatement(stateSlug: string): AssistantReinstatementResult | null {
  const records = reinstatementData.records as Record<string, unknown>[];
  const r = records.find((rec) => rec.state_slug === stateSlug);
  if (!r) return null;
  return {
    state: String(r.state ?? ""),
    state_slug: String(r.state_slug ?? ""),
    reinstatement_fee_usd: typeof r.reinstatement_fee_usd === "number" ? r.reinstatement_fee_usd : null,
    reinstatement_fee_notes: typeof r.reinstatement_fee_notes === "string" && r.reinstatement_fee_notes ? r.reinstatement_fee_notes : null,
    penalty_cpe_hours: typeof r.penalty_cpe_hours === "number" ? r.penalty_cpe_hours : null,
    penalty_cpe_notes: typeof r.penalty_cpe_notes === "string" && r.penalty_cpe_notes ? r.penalty_cpe_notes : null,
    penalty_ethics_hours: typeof r.penalty_ethics_hours === "number" ? r.penalty_ethics_hours : null,
    lapse_trigger: typeof r.lapse_trigger === "string" ? r.lapse_trigger : null,
    data_gap_note: typeof r.data_gap_note === "string" && r.data_gap_note ? r.data_gap_note : null,
    citation: typeof r.citation === "string" ? r.citation : null,
    citation_url: typeof r.citation_url === "string" ? r.citation_url : null,
    last_verified: String(r.last_verified ?? ""),
  };
}

interface AssistantRenewalFeeResult {
  state: string;
  state_slug: string;
  fee_usd: number | null;
  fee_notes: string | null;
  fee_basis: string | null;
  confidence: string | null;
  citation: string | null;
  citation_url: string | null;
  verified_date: string;
}

export function lookupAssistantRenewalFee(stateSlug: string): AssistantRenewalFeeResult | null {
  const records = renewalFeesData.records as Record<string, unknown>[];
  const r = records.find((rec) => rec.state_slug === stateSlug);
  if (!r) return null;
  return {
    state: String(r.state ?? ""),
    state_slug: String(r.state_slug ?? ""),
    fee_usd: typeof r.fee_usd === "number" ? r.fee_usd : null,
    fee_notes: typeof r.fee_notes === "string" && r.fee_notes ? r.fee_notes : null,
    fee_basis: typeof r.fee_basis === "string" ? r.fee_basis : null,
    confidence: typeof r.confidence === "string" ? r.confidence : null,
    citation: typeof r.citation === "string" ? r.citation : null,
    citation_url: typeof r.citation_url === "string" ? r.citation_url : null,
    verified_date: String(r.verified_date ?? ""),
  };
}

export interface AssistantMobilityResult {
  home_state: string;
  target_state: string;
  service_type: string;
  result: MobilityResult;
  disclaimer: string;
  attestation_note: string;
}

/** Builds the safe response envelope around an already-computed
 * evaluateMobility() result -- index.ts's route handler does the actual
 * evaluateMobility() call (it already has MOBILITY_RULES_BY_SLUG built at
 * module load; duplicating that lookup table here would be the same "two
 * copies" risk this whole endpoint set exists to avoid), this just adds the
 * caller-facing framing. MobilityResult (mobility.ts) is already a
 * determination shape (verdict/reasons/citation), never the raw
 * MobilityRuleRow -- no additional field-stripping needed here, unlike the
 * four lookups above which read straight off a raw JSON record. */
export function buildAssistantMobilityResponse(
  homeStateLabel: string,
  targetStateLabel: string,
  serviceType: string,
  result: MobilityResult
): AssistantMobilityResult {
  return {
    home_state: homeStateLabel,
    target_state: targetStateLabel,
    service_type: serviceType,
    result,
    disclaimer:
      "This determination assumes standard licensure with no personal complications -- it is not legal advice.",
    attestation_note:
      "No personal attestation (license standing, substantial equivalence) was supplied for this query, so " +
      "this result reflects the conservative default -- ask the CPA to confirm their own good standing for a " +
      "definitive answer.",
  };
}

interface AssistantRuleChangeResult {
  jurisdiction: string;
  jurisdiction_slug: string;
  topic: string;
  kind: string;
  summary_public: string;
  citation: string | null;
  citation_url: string | null;
  effective_date: string | null;
  status: string | null;
  verified_date: string;
}

export function lookupAssistantRuleChanges(stateSlug: string): AssistantRuleChangeResult[] {
  const events = (regChangeEventsData.events as unknown[]).filter(
    (e): e is Record<string, unknown> => typeof e === "object" && e !== null
  );
  return events
    .filter((e) => e.jurisdiction_slug === stateSlug)
    .map((e) => ({
      jurisdiction: String(e.jurisdiction ?? ""),
      jurisdiction_slug: String(e.jurisdiction_slug ?? ""),
      topic: String(e.topic ?? ""),
      kind: String(e.kind ?? ""),
      // summary_public is the ONE field this dataset's own schema already
      // names as customer-safe (see reg_change_events.json's rule-conflict
      // records) -- unlike the other 4 datasets, this file has no separate
      // internal-vs-public split to get wrong, since every event was
      // authored knowing summary_public specifically ships to a reader.
      summary_public: String(e.summary_public ?? ""),
      citation: typeof e.citation === "string" ? e.citation : null,
      citation_url: typeof e.citation_url === "string" ? e.citation_url : null,
      effective_date: typeof e.effective_date === "string" ? e.effective_date : null,
      status: typeof e.status === "string" ? e.status : null,
      verified_date: String(e.verified_date ?? ""),
    }));
}

export function assistantStateName(stateSlug: string): string | null {
  return stateNameForSlug(stateSlug);
}
