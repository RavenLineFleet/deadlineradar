import { describe, it, expect } from "vitest";
import rulesData from "../src/firm_mobility_rules.json";
import {
  normalizeFirmRuleRow,
  evaluateFirmMobility,
  isFirmRuleStale,
  type FirmMobilityRuleRow,
} from "../src/firm_mobility";

/** GATE-80 (AuditLab 2026-10-31): the three constant headlines, pinned
 * verbatim. SRC-147/149/150 were one defect three times -- a constant that
 * overclaims past its population -- and a phrase list cannot see a reworded
 * recurrence. Each literal is reviewed against every row it serves; editing
 * one here is the review trigger. Do not import these from the source. */
const GATE80_PINNED = {
  officeFalse:
    "Having an office here is not, on its own, what this state's rule turns on. The state's rule below " +
    "says what does require firm registration; check it against your firm's facts.",
  officeTrue:
    "This state's firm-registration rule for a firm with an office here is below; check it against " +
    "your firm's facts.",
  attestFalse:
    "This state's firm-registration rule for attest work by a firm without an office here is below; check it " +
    "against your firm's facts, including where the client is based.",
  attestTrue:
    "This state's firm-registration rule for attest work by a firm without an office here, and the conditions " +
    "of any exemption from it, are below; check them against your firm's facts, including where the client is based.",
};

/**
 * Data-integrity tests for the firm-level registration ruleset (roadmap
 * #318, DiffLab's 2026-08-07/08 research pass). Same split as
 * mobility-data.spec.ts: these do NOT re-test the engine (that's
 * firm-mobility.spec.ts's job) -- they test the DATA, because the data is
 * sourced from outside this repo and a half-normalized row is worse than an
 * absent one.
 *
 * One real discrepancy caught and fixed on the way in, not left for a test
 * to discover later: DiffLab's own dataset keys the District of Columbia as
 * "district-of-columbia", but every other reference dataset in this repo
 * (cpa_deadlines.json, cpe_hours.json, etc.) uses "dc" -- confirmed via a
 * direct diff against cpa_deadlines.json's own 55-slug set, the ONLY
 * divergence found. worker/src/firm_mobility_rules.json remaps it to "dc"
 * (both the object key and the inner state_slug field) so
 * stateNameForSlug() -- which reads from that same canonical list --
 * actually resolves it; left as "district-of-columbia" it would 400 on
 * every real request for DC and the row would sit unreachable forever.
 */

const records = rulesData as Record<string, unknown>;
const CANONICAL_55 = [
  "alabama", "alaska", "arizona", "arkansas", "california", "colorado", "connecticut",
  "delaware", "dc", "florida", "georgia", "guam", "hawaii", "idaho",
  "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana", "maine", "maryland",
  "massachusetts", "michigan", "minnesota", "mississippi", "missouri", "montana", "nebraska",
  "nevada", "new-hampshire", "new-jersey", "new-mexico", "new-york", "north-carolina",
  "north-dakota", "northern-mariana-islands", "ohio", "oklahoma", "oregon", "pennsylvania",
  "puerto-rico", "rhode-island", "south-carolina", "south-dakota", "tennessee", "texas",
  "us-virgin-islands", "utah", "vermont", "virginia", "washington", "west-virginia",
  "wisconsin", "wyoming",
];

describe("firm mobility ruleset -- file shape", () => {
  it("carries all 55 canonical jurisdictions, zero missing, zero extra, using this repo's own slugs", () => {
    const slugs = Object.keys(records).sort();
    expect(slugs).toEqual([...CANONICAL_55].sort());
  });

  it("dc resolves and its inner state_slug field agrees with the object key", () => {
    const dc = records.dc as Record<string, unknown> | undefined;
    expect(dc).toBeDefined();
    expect(dc?.state_slug).toBe("dc");
    expect(dc?.state).toBe("District of Columbia");
  });

  it("no state's inner state_slug field disagrees with its own object key", () => {
    for (const [key, raw] of Object.entries(records)) {
      const slug = (raw as Record<string, unknown>).state_slug;
      expect(slug, `key "${key}" has inner state_slug "${slug}"`).toBe(key);
    }
  });
});

describe("every record survives normalizeFirmRuleRow() with nothing silently dropped", () => {
  for (const [slug, raw] of Object.entries(records)) {
    it(`${slug}: normalizes to a row rather than null`, () => {
      expect(normalizeFirmRuleRow(raw)).not.toBeNull();
    });

    it(`${slug}: no condition's exists field silently degrades a real true/false to null`, () => {
      const row = normalizeFirmRuleRow(raw) as FirmMobilityRuleRow;
      const src = raw as Record<string, Record<string, unknown>>;
      for (const field of ["attest_exemption", "physical_office_trigger", "peer_review_conditions_permit"] as const) {
        const key = field === "attest_exemption" ? "attestExemption" : field === "physical_office_trigger" ? "physicalOfficeTrigger" : "peerReviewConditionsPermit";
        const srcExists = src[field]?.exists;
        if (srcExists === null || srcExists === undefined) continue; // an honest null/omitted key is fine
        expect(row[key].exists, `${slug}.${field}.exists was ${JSON.stringify(srcExists)} but normalized differently`).toBe(srcExists);
      }
    });

    it(`${slug}: any citation present on a condition is substantive and survives`, () => {
      const row = normalizeFirmRuleRow(raw) as FirmMobilityRuleRow;
      const src = raw as Record<string, Record<string, unknown>>;
      for (const [field, key] of [
        ["attest_exemption", "attestExemption"],
        ["physical_office_trigger", "physicalOfficeTrigger"],
        ["peer_review_conditions_permit", "peerReviewConditionsPermit"],
      ] as const) {
        const srcCitation = src[field]?.citation;
        if (typeof srcCitation === "string" && srcCitation.trim().length >= 8) {
          expect(row[key].citation, `${slug}.${field}.citation was dropped`).not.toBeNull();
        }
      }
    });

    it(`${slug}: confidence is a recognised value, not silently nulled`, () => {
      const row = normalizeFirmRuleRow(raw) as FirmMobilityRuleRow;
      expect(["dual_source", "single_source", "unverified"]).toContain(row.confidence);
    });

    it(`${slug}: verified_date parses and is not in the future`, () => {
      const row = normalizeFirmRuleRow(raw) as FirmMobilityRuleRow;
      expect(row.verifiedDate).not.toBeNull();
      const t = Date.parse(row.verifiedDate as string);
      expect(Number.isNaN(t)).toBe(false);
      expect(t).toBeLessThanOrEqual(Date.now() + 86_400_000);
    });
  }
});

describe("locked-in dataset facts -- a change here should only come from a real re-sourcing pass", () => {
  const rows = Object.values(records)
    .map((r) => normalizeFirmRuleRow(r))
    .filter((r): r is FirmMobilityRuleRow => r !== null);

  it("exactly 6 states have a genuinely unclear (null) attest_exemption -- Idaho, Maine, Massachusetts, Ohio, Tennessee, Wisconsin (2026-10-30 thin-source re-verify: ID 54-214(1) vs 54-221(3) unreconciled; WI 442.025(4) is written for persons)", () => {
    const unclear = rows.filter((r) => r.attestExemption.exists === null).map((r) => r.stateSlug).sort();
    expect(unclear).toEqual(["idaho", "maine", "massachusetts", "ohio", "tennessee", "wisconsin"]);
  });

  it("exactly 21 states require firm registration for no-office attest work (attest_exemption=false) -- SRC-154 2026-10-31 vermont to false (its exemption is non-attest only); SRC-140 2026-10-31 flipped 14 split-encoded rows to true; SRC-141 2026-10-31 flipped 13 rows whose own notes say no-office attest work must register back to false; SRC-143 2026-10-31 Alaska to false under its statute-vs-rule source_disagreement", () => {
    const required = rows.filter((r) => r.attestExemption.exists === false).map((r) => r.stateSlug).sort();
    expect(required).toEqual([
      "alaska", "arkansas", "california", "connecticut", "dc", "georgia", "guam", "hawaii", "indiana", "kansas",
      "minnesota", "mississippi", "nebraska", "new-york", "northern-mariana-islands", "puerto-rico",
      "rhode-island", "south-dakota", "us-virgin-islands", "vermont", "west-virginia",
    ]);
  });

  // SRC-141 (AuditLab 2026-10-31): 13 exists:true rows had notes saying
  // no-office attest work MUST register, so the paid check printed "exempt"
  // directly above "you must register". An exempt row's note may name a
  // registration requirement only alongside a real conditional carve-out.
  // GATE-77 (AuditLab 2026-10-31): allow up to three modifier / proper-noun
  // tokens before the noun, so "needs a Guam Firm Permit", "requires a lighter
  // 'out-of-state permit'" and "requires firm registration" are all caught.
  // Negated forms ("does not need a Texas firm license", "needs no Iowa
  // permit") are excluded. On the pre-SRC-141 bytes (bf8f4dbc7) this flags
  // all 13 SRC-141 rows plus Alaska (SRC-143); at the fix it flags none.
  const REQUIRES_REGISTRATION = /\b((?<!\bnot\s)(?<!n't\s)(?:must|needs?|requires?)\s+(?!no\b)(?:(?:a|an|one)\s+)?(?:(?!no\b)[\w'-]+\s+){0,3}?(?:register|registration|permit|license|one)\b|must hold a permit|never exempt|only through a firm registered|never for a firm)/i;
  // UT: "Otherwise a firm must register" follows its exemption (AuditLab ruled correct).
  // OK: names its own 15.12A/15.30 mobility carve-out (AuditLab ruled sound 2026-10-31).
  const CARVE_OUT = /\b(unless|otherwise|carve-out)\b/i;
  it("SRC-141 positive control: the guard pattern catches the pre-fix Kansas and West Virginia phrasing", () => {
    expect(REQUIRES_REGISTRATION.test("any firm without a Kansas office that performs attest services for a Kansas client MUST register -- unconditional trigger")).toBe(true);
    expect(REQUIRES_REGISTRATION.test("relief from the permit specifically for a lone individual practitioner, never for a firm as such")).toBe(true);
    expect(REQUIRES_REGISTRATION.test("a no-office firm doing attest work for a CT-headquartered client needs a permit")).toBe(true);
    // GATE-77 controls: the three phrasings the first version of the pattern missed.
    expect(REQUIRES_REGISTRATION.test("A firm needs a Guam Firm Permit to Practice to provide attest services")).toBe(true);
    expect(REQUIRES_REGISTRATION.test("Attest work for an AK-headquartered client requires a lighter 'out-of-state permit' (notice+fee+verification)")).toBe(true);
    expect(REQUIRES_REGISTRATION.test("Oklahoma requires firm registration in three cases")).toBe(true);
    // Negative control: an ordinary exemption sentence must not trip the guard.
    expect(REQUIRES_REGISTRATION.test("A firm with no office here may perform attest work without a firm permit")).toBe(false);
    expect(REQUIRES_REGISTRATION.test("a firm with no Texas office does not need a Texas firm license")).toBe(false);
    expect(REQUIRES_REGISTRATION.test("needs no Iowa permit for any service")).toBe(false);
  });
  it("SRC-141: no exempt row's note says no-office attest work must register without naming a conditional carve-out", () => {
    const offenders = rows
      .filter((r) => r.attestExemption.exists === true && r.attestExemption.notes)
      .filter((r) => REQUIRES_REGISTRATION.test(r.attestExemption.notes!) && !CARVE_OUT.test(r.attestExemption.notes!))
      .map((r) => r.stateSlug);
    expect(offenders).toEqual([]);
  });

  it("exactly 2 states have an unverified physical_office_trigger -- Guam, Wisconsin (2026-08-17: US Virgin Islands filled in; 2026-10-30: Utah filled in from 58-26a-302(3)(b), 58-26a-501(1)(b))", () => {
    const unclear = rows.filter((r) => r.physicalOfficeTrigger.exists === null).map((r) => r.stateSlug).sort();
    expect(unclear).toEqual(["guam", "wisconsin"]);
  });

  // GATE-78 (AuditLab 2026-10-31): pin the office-false census exactly, and
  // check each physical_office_trigger boolean against its own notes, the
  // way SRC-141 does for attest_exemption. Without this, re-encoding 12
  // office-false rows as true (notes untouched) passed every office guard
  // and served "requires firm registration for a firm with a physical
  // office" above a note saying an office alone is insufficient.
  // 2026-10-31: Massachusetts flipped true -> false (its note says the
  // triggers are reports and the CPA title, "not having an office as such").
  const OFFICE_FALSE = [
    "dc", "hawaii", "idaho", "illinois", "indiana", "iowa", "massachusetts", "new-jersey", "new-york",
    "northern-mariana-islands", "oregon", "puerto-rico", "rhode-island", "us-virgin-islands", "virginia", "washington",
  ];
  it("exactly 16 states say an office alone does not trigger firm registration (physical_office_trigger=false) -- GATE-78 2026-10-31; Massachusetts added 2026-10-31", () => {
    const officeFalse = rows.filter((r) => r.physicalOfficeTrigger.exists === false).map((r) => r.stateSlug).sort();
    expect(officeFalse).toEqual(OFFICE_FALSE);
  });

  // Phrases the notes use to say an office is not sufficient on its own.
  // Every office-false note matches; no office-true note may.
  // GATE-79 (AuditLab 2026-10-31): this is a PHRASE TRIPWIRE, not a check of
  // the rule. It tests which conclusion a note recites, so a one-word
  // synonym ("never" -> "not" in New Mexico's note) flips it, and it passes
  // 21 true rows whose notes condition the duty on attest work or title use.
  // It does not certify the boolean. What keeps the served answer honest is
  // (a) the hand-verified, pinned 16-row false census above, and (b) the
  // office-true summary asserting nothing the boolean would have to be right
  // about (SRC-149 test below), so a true row whose rule is conditional
  // still serves an accurate headline plus its own notes.
  const OFFICE_NOT_SUFFICIENT = /\b(?:not|isn't|is not)\s+(?:truly\s+)?independent\b|\bisn't unconditional\b|regardless of office|\bnot\s+(?:by\s+|on\s+)?(?:having\s+)?an office\b|\bnot\s+(?:mere\s+|bare\s+)?office presence\b|\boffice alone (?:is insufficient|isn't|doesn't|does not)\b|\balone does not trigger\b|\bnot an independent trigger\b|\brather than a separate trigger\b|\bnot a trigger on its own\b|\bno provision makes\b[^.]{0,40}\bindependently trigger/i;
  const officeBooleanOffenders = (rs: FirmMobilityRuleRow[]) =>
    rs
      .filter((r) => r.physicalOfficeTrigger.exists !== null && r.physicalOfficeTrigger.notes)
      .filter((r) => (r.physicalOfficeTrigger.exists === false) !== OFFICE_NOT_SUFFICIENT.test(r.physicalOfficeTrigger.notes!))
      .map((r) => r.stateSlug)
      .sort();
  it("GATE-78: every physical_office_trigger boolean agrees with its own notes", () => {
    expect(officeBooleanOffenders(rows)).toEqual([]);
  });
  it("GATE-78 positive control: AuditLab's mutation (12 office-false rows re-encoded true, notes untouched) is caught", () => {
    const mutated12 = [
      "dc", "idaho", "illinois", "indiana", "iowa", "new-jersey", "new-york",
      "northern-mariana-islands", "oregon", "rhode-island", "us-virgin-islands", "washington",
    ];
    const mutated = rows.map((r) =>
      mutated12.includes(r.stateSlug) ? { ...r, physicalOfficeTrigger: { ...r.physicalOfficeTrigger, exists: true } } : r
    );
    expect(officeBooleanOffenders(mutated)).toEqual(mutated12.slice().sort());
    // And the reverse direction: an office-true row re-encoded false is caught too.
    const flippedTexas = rows.map((r) =>
      r.stateSlug === "texas" ? { ...r, physicalOfficeTrigger: { ...r.physicalOfficeTrigger, exists: false } } : r
    );
    expect(officeBooleanOffenders(flippedTexas)).toEqual(["texas"]);
  });

  it("exactly these states carry a real source_disagreement note (2026-08-17: WV, AZ, NJ, NM, NY, PA, USVI, VT, NH resolved via direct primary-source text -- NE/OH genuinely unresolved; 2026-10-31 SRC-143: AK statute vs unconformed 12 AAC 04.245(a)(2))", () => {
    const disagreeing = rows.filter((r) => r.sourceDisagreement !== null).map((r) => r.stateSlug).sort();
    expect(disagreeing).toEqual(["alaska", "nebraska", "ohio"].sort());
  });
});

describe("the engine's answers are conservative where they should be, across the real dataset", () => {
  const rows = Object.values(records)
    .map((r) => normalizeFirmRuleRow(r))
    .filter((r): r is FirmMobilityRuleRow => r !== null);

  it("no state answers 'clear' while its attest_exemption is unknown (no physical office case)", () => {
    for (const row of rows) {
      if (row.attestExemption.exists === null) {
        const finding = evaluateFirmMobility(
          { firmHomeStateSlug: "ohio", targetStateSlug: row.stateSlug, hasPhysicalOfficeInTargetState: false },
          row,
          null
        );
        expect(finding.verdict, `${row.stateSlug} claimed ${finding.verdict} on an unknown attest exemption`).not.toBe("clear");
      }
    }
  });

  it("no state answers 'clear' while its physical_office_trigger is unknown (has-office case)", () => {
    for (const row of rows) {
      if (row.physicalOfficeTrigger.exists === null) {
        const finding = evaluateFirmMobility(
          { firmHomeStateSlug: "ohio", targetStateSlug: row.stateSlug, hasPhysicalOfficeInTargetState: true },
          row,
          null
        );
        expect(finding.verdict, `${row.stateSlug} claimed ${finding.verdict} on an unknown office trigger`).not.toBe("clear");
      }
    }
  });

  it("every state with a source_disagreement is not_verified, regardless of its other fields", () => {
    for (const row of rows) {
      if (row.sourceDisagreement !== null) {
        // A fixed home state that is never the target itself -- otherwise
        // the home==target not_applicable branch would short-circuit
        // before the source-disagreement check ever runs, for whichever
        // row happens to share a slug with the chosen home state.
        const home = row.stateSlug === "ohio" ? "california" : "ohio";
        const finding = evaluateFirmMobility(
          { firmHomeStateSlug: home, targetStateSlug: row.stateSlug, hasPhysicalOfficeInTargetState: false },
          row,
          null
        );
        expect(finding.verdict, `${row.stateSlug} claimed ${finding.verdict} despite a source disagreement`).toBe("not_verified");
      }
    }
  });

  it("SRC-145: no state answers 'clear' for a firm with an office there; every office-false row serves its own trigger notes", () => {
    const officeFalse = rows.filter((r) => r.physicalOfficeTrigger.exists === false);
    expect(officeFalse.length).toBe(16); // GATE-78: pinned to the census above, not just non-empty
    for (const row of rows) {
      const home = row.stateSlug === "texas" ? "california" : "texas";
      const finding = evaluateFirmMobility(
        { firmHomeStateSlug: home, targetStateSlug: row.stateSlug, hasPhysicalOfficeInTargetState: true },
        row,
        null
      );
      expect(finding.verdict, `${row.stateSlug} told an office-having firm 'clear'`).not.toBe("clear");
      if (row.physicalOfficeTrigger.exists === false && !row.sourceDisagreement) {
        expect(finding.verdict, row.stateSlug).toBe("depends_on_facts");
        expect(finding.requirements, row.stateSlug).toEqual([row.physicalOfficeTrigger.notes]);
      }
    }
  });

  it("SRC-152: an attest-exempt row with blank notes is not_verified, never an answer with no conditions", () => {
    // nevada: attest exemption true, peer-review permit not true, so no later
    // limb would catch a blank note.
    const nv = rows.find((r) => r.stateSlug === "nevada")!;
    expect(nv.attestExemption.exists).toBe(true);
    expect(nv.peerReviewConditionsPermit.exists).not.toBe(true);
    // Positive control: the live row answers (depends_on_facts since SRC-153) and shows its notes.
    const live = evaluateFirmMobility(
      { firmHomeStateSlug: "texas", targetStateSlug: "nevada", hasPhysicalOfficeInTargetState: false },
      nv,
      null
    );
    expect(live.verdict).toBe("depends_on_facts");
    expect(live.requirements).toEqual([nv.attestExemption.notes]);
    for (const notes of [null, "", "   ", "\n\t "]) {
      const finding = evaluateFirmMobility(
        { firmHomeStateSlug: "texas", targetStateSlug: "nevada", hasPhysicalOfficeInTargetState: false },
        { ...nv, attestExemption: { ...nv.attestExemption, notes } },
        null
      );
      expect(finding.verdict, JSON.stringify(notes)).toBe("not_verified");
    }
  });

  it("SRC-147: the office-false summary names no trigger -- what the trigger is lives only in each row's notes", () => {
    // hawaii / puerto-rico / northern-mariana-islands attach registration to
    // any practice regardless of office; iowa / washington / virginia name
    // different trigger sets. A constant headline naming attest work or the
    // CPA title contradicts or overreaches on those rows.
    const officeFalse = rows.filter((r) => r.physicalOfficeTrigger.exists === false && r.physicalOfficeTrigger.notes);
    expect(officeFalse.map((r) => r.stateSlug)).toEqual(expect.arrayContaining(["hawaii", "puerto-rico", "virginia"]));
    expect(officeFalse.length).toBe(16); // GATE-78
    const summaries = new Set<string>();
    let checked = 0;
    for (const row of officeFalse) {
      const home = row.stateSlug === "texas" ? "california" : "texas";
      const finding = evaluateFirmMobility(
        { firmHomeStateSlug: home, targetStateSlug: row.stateSlug, hasPhysicalOfficeInTargetState: true },
        row,
        null
      );
      if (finding.verdict !== "depends_on_facts") continue; // disagreement rows render their own text
      expect(finding.summary, row.stateSlug).not.toMatch(/attest|title|titling|principal place|depends on what the office does/i);
      summaries.add(finding.summary);
      checked++;
    }
    expect(checked, "GATE-78: every office-false row must reach the summary check").toBe(officeFalse.length);
    // GATE-80: pinned verbatim. The phrase check above is a tripwire only; a
    // reword that keeps clear of it can still overclaim. Any change to this
    // text must edit this literal, so it is a reviewed diff, never silent.
    expect([...summaries]).toEqual([GATE80_PINNED.officeFalse]);
  });

  it("SRC-149: the office-true summary asserts nothing beyond what is common to all 37 true rows; every one serves its own notes", () => {
    // 21 true rows condition registration on attest work and/or CPA-title use
    // as well as the office (new-mexico: "never bare office presence alone").
    // A headline saying the office requires registration contradicts them.
    const BANNED = /requires?|must|regardless|physical office|attest|title|titling|alone|by itself/i;
    // Positive control: the pre-fix headline is caught by BANNED.
    expect(BANNED.test("This state requires firm registration for a firm with a physical office here.")).toBe(true);
    const officeTrue = rows.filter((r) => r.physicalOfficeTrigger.exists === true);
    expect(officeTrue.length).toBe(37);
    expect(officeTrue.map((r) => r.stateSlug)).toEqual(expect.arrayContaining(["new-mexico", "nevada", "south-dakota", "utah"]));
    const summaries = new Set<string>();
    let checked = 0;
    for (const row of officeTrue) {
      const home = row.stateSlug === "texas" ? "california" : "texas";
      const finding = evaluateFirmMobility(
        { firmHomeStateSlug: home, targetStateSlug: row.stateSlug, hasPhysicalOfficeInTargetState: true },
        row,
        null
      );
      expect(finding.verdict, row.stateSlug).toBe("depends_on_facts");
      expect(finding.summary, row.stateSlug).not.toMatch(BANNED);
      expect(finding.requirements, row.stateSlug).toEqual([row.physicalOfficeTrigger.notes]);
      summaries.add(finding.summary);
      checked++;
    }
    expect(checked, "every office-true row must reach the summary check").toBe(officeTrue.length);
    expect(summaries.size, "the office-true summary is one constant").toBe(1);
    expect([...summaries], "GATE-80: pinned verbatim").toEqual([GATE80_PINNED.officeTrue]);
  });

  it("SRC-149: an office-true row with no notes is not_verified, never an invented requirement", () => {
    const tx = rows.find((r) => r.stateSlug === "texas")!;
    expect(tx.physicalOfficeTrigger.exists).toBe(true);
    const noNotes = { ...tx, physicalOfficeTrigger: { ...tx.physicalOfficeTrigger, notes: null } };
    const finding = evaluateFirmMobility(
      { firmHomeStateSlug: "california", targetStateSlug: "texas", hasPhysicalOfficeInTargetState: true },
      noNotes,
      null
    );
    expect(finding.verdict).toBe("not_verified");
    expect(finding.requirements.join(" ")).not.toMatch(/Register the firm/);
  });

  it("SRC-150: the no-office attest-false summary asserts no unconditional duty; every row serves its own notes", () => {
    // 10 of the rows serving this branch condition the duty on the CLIENT's
    // location (e.g. california: "for a California-headquartered entity"),
    // which the input cannot know. The headline may not assert a duty.
    const BANNED = /requires?|must|even without|regardless|always|every|any attest/i;
    // Positive control: the pre-fix headline is caught by BANNED.
    expect(BANNED.test("This state requires firm registration for attest work, even without a physical office here.")).toBe(true);
    const attestFalse = rows.filter((r) => r.attestExemption.exists === false);
    expect(attestFalse.length).toBe(21);
    const CLIENT_CONDITIONED = [
      "arkansas", "california", "connecticut", "dc", "kansas", "minnesota", "mississippi",
      "south-dakota", "us-virgin-islands", "west-virginia",
    ];
    expect(attestFalse.map((r) => r.stateSlug)).toEqual(expect.arrayContaining(CLIENT_CONDITIONED));
    const summaries = new Set<string>();
    const suppressed: string[] = [];
    let checked = 0;
    for (const row of attestFalse) {
      const home = row.stateSlug === "texas" ? "california" : "texas";
      const finding = evaluateFirmMobility(
        { firmHomeStateSlug: home, targetStateSlug: row.stateSlug, hasPhysicalOfficeInTargetState: false },
        row,
        null
      );
      if (finding.verdict === "not_verified") {
        suppressed.push(row.stateSlug);
        continue;
      }
      expect(finding.verdict, row.stateSlug).toBe("depends_on_facts");
      expect(finding.summary, row.stateSlug).not.toMatch(BANNED);
      expect(finding.requirements, row.stateSlug).toEqual([row.attestExemption.notes]);
      summaries.add(finding.summary);
      checked++;
    }
    // Discriminating control: only the attest-scoped source disagreements suppress.
    expect(suppressed.sort()).toEqual(["alaska", "nebraska"]);
    expect(checked).toBe(19); // SRC-154: vermont moved here from attest-true
    expect(summaries.size, "the attest-false summary is one constant").toBe(1);
    expect([...summaries], "GATE-80: pinned verbatim").toEqual([GATE80_PINNED.attestFalse]);
  });

  it("SRC-153: the no-office attest-TRUE path never answers clear; one constant headline that names no exemption or condition", () => {
    // 26 of the 28 peer-review-conditioned rows name conditions beyond peer
    // review, and 6 say a no-office firm doing attest work for an in-state
    // client DOES register unless a narrow carve-out applies. The input knows
    // none of that. (SRC-154: vermont's exemption is non-attest only, so it
    // is now exists:false; the other 5 keep a narrow attest carve-out.)
    const BANNED = /exempts?|conditioned on|requires?|must|not required|no requirement|haven't identified|is current|peer.review/i;
    // Positive controls: both pre-fix headlines are caught by BANNED.
    expect(BANNED.test(
      "This state exempts attest work by a no-office firm from registration, conditioned on peer-review " +
      "compliance -- your firm's on-file peer-review due date is current (self-reported, not independently verified)."
    )).toBe(true);
    expect(BANNED.test("We haven't identified a firm-registration requirement in this state for attest work by a no-office firm.")).toBe(true);
    const attestTrue = rows.filter((r) => r.attestExemption.exists === true);
    expect(attestTrue.length).toBe(28);
    const OVERCLAIMED = ["new-jersey", "florida", "new-hampshire", "south-carolina", "oklahoma"];
    expect(attestTrue.map((r) => r.stateSlug)).toEqual(expect.arrayContaining([...OVERCLAIMED, "nevada"]));
    const future = new Date(Date.now() + 90 * 86_400_000).toISOString().slice(0, 10);
    const past = "2020-01-01";
    const summaries = new Set<string>();
    const suppressed = new Set<string>();
    let checked = 0;
    for (const row of attestTrue) {
      const home = row.stateSlug === "texas" ? "california" : "texas";
      for (const due of [future, past, null]) {
        const finding = evaluateFirmMobility(
          { firmHomeStateSlug: home, targetStateSlug: row.stateSlug, hasPhysicalOfficeInTargetState: false },
          row,
          due
        );
        expect(finding.verdict, `${row.stateSlug} @ ${due}`).not.toBe("clear");
        if (finding.verdict === "not_verified") {
          suppressed.add(row.stateSlug);
          continue;
        }
        expect(finding.verdict, `${row.stateSlug} @ ${due}`).toBe("depends_on_facts");
        expect(finding.summary, row.stateSlug).not.toMatch(BANNED);
        expect(finding.requirements[0], `${row.stateSlug}: exemption conditions lead`).toBe(row.attestExemption.notes);
        if (row.peerReviewConditionsPermit.exists === true) {
          expect(finding.requirements[1], row.stateSlug).toBe(row.peerReviewConditionsPermit.notes);
          expect(finding.requirements.length, row.stateSlug).toBe(3);
          expect(finding.requirements[2], `${row.stateSlug} @ ${due}`).toMatch(
            due === future ? /is current/ : due === past ? /has passed/ : /no peer-review due date on file/
          );
        } else {
          expect(finding.requirements, row.stateSlug).toEqual([row.attestExemption.notes]);
        }
        summaries.add(finding.summary);
        checked++;
      }
    }
    // Discriminating control: nothing on live data is suppressed, so all 28 x 3 answer.
    expect([...suppressed]).toEqual([]);
    expect(checked).toBe(attestTrue.length * 3);
    expect(summaries.size, "the attest-true summary is one constant").toBe(1);
    expect([...summaries], "GATE-80: pinned verbatim").toEqual([GATE80_PINNED.attestTrue]);
  });

  it("SRC-153: an uncited attest-exemption row is still not_verified now that the path no longer answers clear", () => {
    const nv = rows.find((r) => r.stateSlug === "nevada")!;
    const input = { firmHomeStateSlug: "texas", targetStateSlug: "nevada", hasPhysicalOfficeInTargetState: false };
    // Positive control: cited live row answers.
    expect(evaluateFirmMobility(input, nv, null).verdict).toBe("depends_on_facts");
    for (const citation of [null, "", "   "]) {
      const finding = evaluateFirmMobility(input, { ...nv, attestExemption: { ...nv.attestExemption, citation } }, null);
      expect(finding.verdict, JSON.stringify(citation)).toBe("not_verified");
      expect(finding.citation).toBeNull();
    }
  });

  it("SRC-151: a null, empty, or whitespace-only note on any limb is not_verified, never a blank or invented requirement", () => {
    const BLANKS: (string | null)[] = [null, "", "   ", "\n\t "];
    const tx = rows.find((r) => r.stateSlug === "texas")!;
    const officeFalseRow = rows.find((r) => r.physicalOfficeTrigger.exists === false && !r.sourceDisagreement)!;
    const ca = rows.find((r) => r.stateSlug === "california")!;
    expect(ca.attestExemption.exists).toBe(false);
    const permitRow = rows.find(
      (r) => r.attestExemption.exists === true && r.peerReviewConditionsPermit.exists === true && !r.sourceDisagreement
    )!;
    expect(permitRow, "need a peer-review-conditioned row for the permit limb").toBeDefined();
    const cases: { label: string; row: FirmMobilityRuleRow; office: boolean }[] = [];
    for (const blank of BLANKS) {
      const n = blank as string; // engine must tolerate null too
      cases.push({ label: `office-true ${JSON.stringify(blank)}`, row: { ...tx, physicalOfficeTrigger: { ...tx.physicalOfficeTrigger, notes: n } }, office: true });
      cases.push({ label: `office-false ${JSON.stringify(blank)}`, row: { ...officeFalseRow, physicalOfficeTrigger: { ...officeFalseRow.physicalOfficeTrigger, notes: n } }, office: true });
      cases.push({ label: `attest-false ${JSON.stringify(blank)}`, row: { ...ca, attestExemption: { ...ca.attestExemption, notes: n } }, office: false });
      cases.push({ label: `permit ${JSON.stringify(blank)}`, row: { ...permitRow, peerReviewConditionsPermit: { ...permitRow.peerReviewConditionsPermit, notes: n } }, office: false });
    }
    for (const c of cases) {
      const home = c.row.stateSlug === "texas" ? "california" : "texas";
      const finding = evaluateFirmMobility(
        { firmHomeStateSlug: home, targetStateSlug: c.row.stateSlug, hasPhysicalOfficeInTargetState: c.office },
        c.row,
        "2099-01-01"
      );
      expect(finding.verdict, c.label).toBe("not_verified");
      for (const req of finding.requirements) expect(req.trim(), c.label).not.toBe("");
      expect(finding.requirements.join(" "), c.label).not.toMatch(/Register the firm|confirm your firm's peer review is current/);
    }
    expect(cases.length).toBe(16);
  });

  it("SRC-146: exactly AK/NE/OH scope their disagreement to the attest limb, and their office answer is not suppressed", () => {
    const scoped = rows
      .filter((r) => r.sourceDisagreement && r.sourceDisagreementScope === "attest_exemption")
      .map((r) => r.stateSlug)
      .sort();
    expect(scoped).toEqual(["alaska", "nebraska", "ohio"]);
    for (const slug of scoped) {
      const row = rows.find((r) => r.stateSlug === slug)!;
      expect(row.physicalOfficeTrigger.exists, slug).toBe(true);
      const office = evaluateFirmMobility(
        { firmHomeStateSlug: "texas", targetStateSlug: slug, hasPhysicalOfficeInTargetState: true },
        row,
        null
      );
      expect(office.verdict, slug).toBe("depends_on_facts");
      const noOffice = evaluateFirmMobility(
        { firmHomeStateSlug: "texas", targetStateSlug: slug, hasPhysicalOfficeInTargetState: false },
        row,
        null
      );
      expect(noOffice.verdict, slug).toBe("not_verified");
    }
  });

  it("a state with no record at all is not_verified, never a guess", () => {
    const finding = evaluateFirmMobility(
      { firmHomeStateSlug: "ohio", targetStateSlug: "atlantis", hasPhysicalOfficeInTargetState: false },
      null,
      null
    );
    expect(finding.verdict).toBe("not_verified");
  });

  it("every row is inside its verification TTL today", () => {
    for (const row of rows) {
      expect(isFirmRuleStale(row), `${row.stateSlug} is already past the TTL`).toBe(false);
    }
  });

  it("every conditionally-exempt state (peer_review_conditions_permit=true) actually surfaces the requirement text", () => {
    // The exact case the design pivot in the plan exists to protect: a
    // peer-review condition must never be silently absorbed into a bare
    // "clear" with no visible requirement.
    for (const row of rows) {
      if (row.attestExemption.exists === true && row.peerReviewConditionsPermit.exists === true) {
        const future = new Date(Date.now() + 90 * 86_400_000).toISOString().slice(0, 10);
        const finding = evaluateFirmMobility(
          { firmHomeStateSlug: "ohio", targetStateSlug: row.stateSlug, hasPhysicalOfficeInTargetState: false },
          row,
          future
        );
        expect(finding.requirements.length, `${row.stateSlug} has a peer-review condition but no visible requirement`).toBeGreaterThan(0);
        if (row.peerReviewConditionsPermit.notes) {
          expect(finding.requirements, `${row.stateSlug} dropped its peer-review condition text`).toContain(row.peerReviewConditionsPermit.notes);
        }
      }
    }
  });

  it("SRC-140: every exempt state (attest_exemption=true) surfaces the exemption's own conditions, whatever the peer-review outcome", () => {
    // Before SRC-140 the exists===true branch pushed only the peer-review
    // note, so conditions like "through a practice-privileged individual"
    // or ownership tests never reached the paid verdict.
    const future = new Date(Date.now() + 90 * 86_400_000).toISOString().slice(0, 10);
    for (const row of rows) {
      if (row.attestExemption.exists !== true || !row.attestExemption.notes) continue;
      const home = row.stateSlug === "ohio" ? "california" : "ohio";
      for (const due of [future, null]) {
        const finding = evaluateFirmMobility(
          { firmHomeStateSlug: home, targetStateSlug: row.stateSlug, hasPhysicalOfficeInTargetState: false },
          row,
          due
        );
        expect(finding.requirements, `${row.stateSlug} (${finding.verdict}, due=${due}) hid its exemption conditions`).toContain(row.attestExemption.notes);
      }
    }
  });
});

describe("SRC-154: the firm verdict never asserts an outcome the engine can't decide", () => {
  const rows = Object.values(records)
    .map((r) => normalizeFirmRuleRow(r))
    .filter((r): r is NonNullable<typeof r> => r !== null);

  it("every verified-row answer is depends_on_facts; nothing is clear or action_required", () => {
    const future = new Date(Date.now() + 90 * 86_400_000).toISOString().slice(0, 10);
    const seen: Record<string, number> = {};
    for (const row of rows) {
      const home = row.stateSlug === "texas" ? "california" : "texas";
      for (const office of [true, false]) {
        for (const due of [future, "2020-01-01", null]) {
          const v = evaluateFirmMobility(
            { firmHomeStateSlug: home, targetStateSlug: row.stateSlug, hasPhysicalOfficeInTargetState: office },
            row,
            due
          ).verdict as string;
          seen[v] = (seen[v] ?? 0) + 1;
        }
      }
    }
    expect(Object.keys(seen).sort()).toEqual(["depends_on_facts", "not_verified"]);
    expect(seen.depends_on_facts ?? 0).toBeGreaterThan(seen.not_verified ?? 0);
  });

  it("vermont is exists:false -- its note documents a NON-attest exemption only", () => {
    const vt = rows.find((r) => r.stateSlug === "vermont")!;
    expect(vt.attestExemption.exists).toBe(false);
    expect(vt.attestExemption.notes).toMatch(/no-office firm performing attest-type services for a VT-home-office client/);
    const f = evaluateFirmMobility(
      { firmHomeStateSlug: "texas", targetStateSlug: "vermont", hasPhysicalOfficeInTargetState: false },
      vt,
      null
    );
    expect(f.verdict).toBe("depends_on_facts");
    expect(f.summary).toBe(GATE80_PINNED.attestFalse);
    expect(f.requirements).toEqual([vt.attestExemption.notes]); // no peer-review line
  });
});
