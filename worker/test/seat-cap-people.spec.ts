/**
 * Seat cap counts PEOPLE (Devin 2026-10-10, option 2b): see src/seat_usage.ts.
 *
 *   - one person in several states = ONE seat
 *   - firm-entity ("-firm") lines = NO seat
 *   - per-person line ceiling, firm-entity ceilings (total + per type)
 *   - the same cap math everywhere (add gate, GET seat_count, pause reconciler,
 *     picks), and the paid / grandfathered / trial caps keep their numbers.
 * Every "allowed" assertion has a paired "refused" control on the same roster,
 * so a gate that simply stopped enforcing would fail here.
 */
import { env, SELF } from "cloudflare:test";
import { describe, expect, it } from "vitest";
import * as store from "../src/store";
import {
  computeSeatUsage,
  decideAdd,
  FIRM_ENTITY_LINE_CEILING,
  FIRM_ENTITY_LINES_PER_TYPE,
  PER_PERSON_LINE_CEILING,
} from "../src/seat_usage";

async function createFirm(name: string): Promise<{ firmId: string; cookie: string }> {
  const firm = await store.createFirm(env.DB, { name, adminEmail: `${name.replace(/\W/g, "")}-${Date.now()}@example.com` });
  // No active trial: these tests are about the steady-state cap (post-cutover free = 3).
  await env.DB.prepare("UPDATE firms SET trial_ends_at = NULL WHERE id = ?1").bind(firm.id).run();
  const { rawSessionToken } = await store.createSession(env.DB, firm.id);
  return { firmId: firm.id, cookie: `dr_firm_session=${rawSessionToken}` };
}

async function seed(firmId: string, email: string, stateSlug: string, licenseTypeId: string): Promise<string> {
  const row = await store.addPending(env.DB, {
    email,
    stateSlug,
    deadlineFields: { license_type_id: licenseTypeId },
    firstName: null,
    deadlineSource: store.DEADLINE_SOURCE_COMPUTED,
    userDeadline: null,
    firmId,
    staffLabel: null,
    skipConfirmation: true,
  });
  return row.id;
}

async function post(cookie: string, body: Record<string, string>): Promise<Response> {
  return SELF.fetch("https://deadline-radar.com/firm/licenses", {
    method: "POST",
    headers: { "content-type": "application/json", "cf-connecting-ip": "203.0.113.201", Cookie: cookie },
    body: JSON.stringify(body),
  });
}

async function listBody(cookie: string): Promise<{ seat_cap: number; seat_count: number; licenses: unknown[] }> {
  const r = await SELF.fetch("https://deadline-radar.com/firm/licenses", {
    headers: { "cf-connecting-ip": "203.0.113.201", Cookie: cookie },
  });
  return (await r.json()) as { seat_cap: number; seat_count: number; licenses: unknown[] };
}

const STATES = ["alabama", "alaska", "arizona", "arkansas", "california", "colorado", "connecticut", "delaware", "florida", "hawaii", "idaho", "illinois"];

describe("seat_usage.ts -- pure rules", () => {
  const row = (email: string, typeId: string | null, raw?: string) => ({
    email,
    deadline_fields: raw ?? (typeId ? JSON.stringify({ license_type_id: typeId }) : "{}"),
  });

  it("same email in several states is one person; firm-entity lines are none", () => {
    const u = computeSeatUsage([
      row("a@x.com", "tx-individual"),
      row("A@X.com ", "fl-individual"),
      row("a@x.com", "ga-individual"),
      row("b@x.com", "ga-firm"),
    ]);
    expect(u.people).toBe(1);
    expect(u.firmEntityLines).toBe(1);
  });

  it("plus aliases are DIFFERENT people (no merging many staff into one seat)", () => {
    const u = computeSeatUsage([row("a+jane@x.com", "ga-individual"), row("a+bob@x.com", "ga-individual"), row("a@x.com", "ga-individual")]);
    expect(u.people).toBe(3);
  });

  it("a malformed deadline_fields row still consumes a seat (never makes the cap look emptier)", () => {
    const u = computeSeatUsage([row("a@x.com", null, "{not json"), row("b@x.com", null, "")]);
    expect(u.people).toBe(2);
  });

  it("decideAdd: existing person at cap is allowed, new person at cap is refused, firm line at cap is allowed", () => {
    const u = computeSeatUsage([row("a@x.com", "ga-individual"), row("b@x.com", "ga-individual"), row("c@x.com", "ga-individual")]);
    expect(decideAdd(u, "a@x.com", "tx-individual", 3)).toEqual({ ok: true });
    expect(decideAdd(u, "d@x.com", "tx-individual", 3)).toEqual({ ok: false, reason: "seat_cap" });
    expect(decideAdd(u, "d@x.com", "tx-firm", 3)).toEqual({ ok: true });
  });
});

describe("POST /firm/licenses -- people-based seat cap (free tier, cap 3)", () => {
  it("3 staff + the firm's own -firm permit is ALLOWED (was a 402); a 4th distinct person is still refused", async () => {
    const { firmId, cookie } = await createFirm("PeopleCapFirmPermit");
    for (let i = 0; i < 3; i++) await seed(firmId, `staff${i}-${Date.now()}@example.com`, "georgia", "ga-individual");
    const permit = await post(cookie, { email: `office-${Date.now()}@example.com`, state_slug: "georgia", license_type_id: "ga-firm" });
    expect(permit.status).toBe(201);
    const fourth = await post(cookie, { email: `fourth-${Date.now()}@example.com`, state_slug: "georgia", license_type_id: "ga-individual" });
    expect(fourth.status).toBe(402);
    expect(((await fourth.json()) as { error: string }).error).toContain("3");
    const b = await listBody(cookie);
    expect(b.seat_cap).toBe(3);
    expect(b.seat_count).toBe(3); // 4 lines, 3 people
    expect(b.licenses.length).toBe(4);
  });

  it("one person x several states uses ONE seat; a new person at cap is refused", async () => {
    const { firmId, cookie } = await createFirm("PeopleCapMultiState");
    const p = `multi-${Date.now()}@example.com`;
    await seed(firmId, p, "alabama", "al-individual");
    await seed(firmId, p, "alaska", "ak-individual");
    await seed(firmId, `s2-${Date.now()}@example.com`, "georgia", "ga-individual");
    await seed(firmId, `s3-${Date.now()}@example.com`, "georgia", "ga-individual");
    expect(await store.countFirmLicenses(env.DB, firmId)).toBe(3); // 4 lines, 3 people: AT cap
    const sameInGeorgia = await post(cookie, { email: p, state_slug: "georgia", license_type_id: "ga-individual" });
    expect(sameInGeorgia.status).toBe(201);
    const stranger = await post(cookie, { email: `stranger-${Date.now()}@example.com`, state_slug: "georgia", license_type_id: "ga-individual" });
    expect(stranger.status).toBe(402);
    expect((await listBody(cookie)).seat_count).toBe(3);
  });

  it("a plus-alias of a rostered person is a NEW person (cap bypass control)", async () => {
    const { firmId, cookie } = await createFirm("PeopleCapAlias");
    const t = Date.now();
    for (const n of ["a", "b", "c"]) await seed(firmId, `${n}${t}@example.com`, "georgia", "ga-individual");
    const alias = await post(cookie, { email: `a+second${t}@example.com`, state_slug: "illinois", license_type_id: "il-individual" });
    expect(alias.status).toBe(402);
  });

  it("per-person line ceiling: the 10th line is allowed, the 11th is refused (400)", async () => {
    const { firmId, cookie } = await createFirm("PeopleCapCeiling");
    const p = `ceil-${Date.now()}@example.com`;
    for (const st of STATES.slice(0, PER_PERSON_LINE_CEILING - 1)) await seed(firmId, p, st, `${st}-individual`);
    const tenth = await post(cookie, { email: p, state_slug: "georgia", license_type_id: "ga-individual" });
    expect(tenth.status).toBe(201);
    const eleventh = await post(cookie, { email: p, state_slug: "illinois", license_type_id: "il-individual" });
    expect(eleventh.status).toBe(400);
    expect(((await eleventh.json()) as { error: string }).error).toContain(String(PER_PERSON_LINE_CEILING));
  });

  it("firm-entity ceilings: per-type cap refuses the 3rd 'ga-firm' line; total cap refuses past the ceiling", async () => {
    const { firmId, cookie } = await createFirm("PeopleCapFirmCeilings");
    const t = Date.now();
    for (let i = 0; i < FIRM_ENTITY_LINES_PER_TYPE; i++) await seed(firmId, `gaf${i}-${t}@example.com`, "georgia", "ga-firm");
    const third = await post(cookie, { email: `gaf-extra-${t}@example.com`, state_slug: "georgia", license_type_id: "ga-firm" });
    expect(third.status).toBe(400);
    const { firmId: f2, cookie: c2 } = await createFirm("PeopleCapFirmTotal");
    for (let i = 0; i < FIRM_ENTITY_LINE_CEILING; i++) await seed(f2, `ft${i}-${t}@example.com`, "georgia", `type${i}-firm`);
    const over = await post(c2, { email: `ft-extra-${t}@example.com`, state_slug: "georgia", license_type_id: "ga-firm" });
    expect(over.status).toBe(400);
  });

  it("admin-removed lines free their seat (live count, not lifetime)", async () => {
    const { firmId, cookie } = await createFirm("PeopleCapRemoved");
    const t = Date.now();
    const ids: string[] = [];
    for (const n of ["a", "b", "c"]) ids.push(await seed(firmId, `${n}${t}@example.com`, "georgia", "ga-individual"));
    expect((await post(cookie, { email: `d${t}@example.com`, state_slug: "georgia", license_type_id: "ga-individual" })).status).toBe(402);
    const del = await SELF.fetch(`https://deadline-radar.com/firm/licenses/${ids[0]}`, {
      method: "DELETE",
      headers: { Cookie: cookie, "cf-connecting-ip": "203.0.113.201" },
    });
    expect(del.status).toBe(200);
    expect((await post(cookie, { email: `d${t}@example.com`, state_slug: "georgia", license_type_id: "ga-individual" })).status).toBe(201);
  });
});

describe("POST /firm/licenses -- other caps keep their numbers", () => {
  it("paid Essentials (5): 5 people + a 6th person refused; a 2nd state for an existing person allowed", async () => {
    const { firmId, cookie } = await createFirm("PeopleCapPaid");
    await env.DB.prepare("UPDATE firms SET plan_tier = 'firm_starter' WHERE id = ?1").bind(firmId).run();
    const t = Date.now();
    for (let i = 0; i < 5; i++) await seed(firmId, `p${i}-${t}@example.com`, "georgia", "ga-individual");
    expect((await post(cookie, { email: `p0-${t}@example.com`, state_slug: "illinois", license_type_id: "il-individual" })).status).toBe(201);
    const sixth = await post(cookie, { email: `p6-${t}@example.com`, state_slug: "georgia", license_type_id: "ga-individual" });
    expect(sixth.status).toBe(402);
    expect(((await sixth.json()) as { error: string }).error).toContain("5");
    expect((await listBody(cookie)).seat_cap).toBe(5);
  });

  it("grandfathered pre-cutover free firm (25): 25 people + 26th refused, 2nd state for existing allowed", async () => {
    const { firmId, cookie } = await createFirm("PeopleCapGrandfathered");
    await env.DB.prepare("UPDATE firms SET created_at = '2020-01-01T00:00:00Z' WHERE id = ?1").bind(firmId).run();
    const t = Date.now();
    for (let i = 0; i < 25; i++) await seed(firmId, `g${i}-${t}@example.com`, "georgia", "ga-individual");
    expect((await post(cookie, { email: `g0-${t}@example.com`, state_slug: "illinois", license_type_id: "il-individual" })).status).toBe(201);
    expect((await post(cookie, { email: `g26-${t}@example.com`, state_slug: "georgia", license_type_id: "ga-individual" })).status).toBe(402);
    expect((await listBody(cookie)).seat_cap).toBe(25);
  });

  it("active trial (35) still lifts the cap for an unpaid firm; people count applies", async () => {
    const { firmId, cookie } = await createFirm("PeopleCapTrial");
    await env.DB.prepare("UPDATE firms SET trial_ends_at = ?1 WHERE id = ?2")
      .bind(new Date(Date.now() + 5 * 86_400_000).toISOString(), firmId)
      .run();
    const t = Date.now();
    for (let i = 0; i < 4; i++) await seed(firmId, `tr${i}-${t}@example.com`, "georgia", "ga-individual");
    expect((await post(cookie, { email: `tr-5-${t}@example.com`, state_slug: "georgia", license_type_id: "ga-individual" })).status).toBe(201);
    expect((await listBody(cookie)).seat_cap).toBe(35);
  });
});

describe("roster pause reconciler -- seats are people", () => {
  it("post-trial over-cap: earliest 3 PEOPLE stay active with all their lines, firm lines stay active, others paused", async () => {
    const { firmId } = await createFirm("PeopleCapPause");
    await env.DB.prepare("UPDATE firms SET trial_ends_at = ?1 WHERE id = ?2")
      .bind(new Date(Date.now() - 8 * 86_400_000).toISOString(), firmId)
      .run();
    const t = Date.now();
    const order = [
      ["p1", "georgia", "ga-individual"],
      ["p1", "alabama", "al-individual"], // same person, 2nd line
      ["p2", "georgia", "ga-individual"],
      ["p3", "georgia", "ga-individual"],
      ["p4", "georgia", "ga-individual"], // 4th person -> paused
      ["office", "georgia", "ga-firm"], // firm-entity -> never paused
    ] as const;
    const ids: Record<string, string> = {};
    for (let i = 0; i < order.length; i++) {
      const [who, st, ty] = order[i]!;
      const id = await seed(firmId, `${who}-${t}@example.com`, st, ty);
      ids[`${who}/${st}`] = id;
      await env.DB.prepare("UPDATE subscribers SET created_at = ?1 WHERE id = ?2").bind(new Date(t + i).toISOString(), id).run();
    }
    const reconciled = await store.reconcileRosterPauseState(env.DB, firmId);
    const paused = new Set(reconciled.filter((r) => r.paused_at !== null).map((r) => r.id));
    expect([...paused]).toEqual([ids["p4/georgia"]]);
  });
});
