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
import { runTrialEndingAlertPass } from "../src/scheduler";
import { addViolatesAfterInsert, computeSeatUsage, decideAdd } from "../src/seat_usage";

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
    for (const st of STATES.slice(0, 9)) await seed(firmId, p, st, `${st}-individual`);
    const tenth = await post(cookie, { email: p, state_slug: "georgia", license_type_id: "ga-individual" });
    expect(tenth.status).toBe(201);
    const eleventh = await post(cookie, { email: p, state_slug: "illinois", license_type_id: "il-individual" });
    expect(eleventh.status).toBe(400);
    expect(((await eleventh.json()) as { error: string }).error).toContain("10");
  });

  it("firm-entity ceilings: per-type cap refuses the 3rd 'ga-firm' line; total cap refuses past the ceiling", async () => {
    const { firmId, cookie } = await createFirm("PeopleCapFirmCeilings");
    const t = Date.now();
    for (let i = 0; i < 2; i++) await seed(firmId, `gaf${i}-${t}@example.com`, "georgia", "ga-firm");
    const third = await post(cookie, { email: `gaf-extra-${t}@example.com`, state_slug: "georgia", license_type_id: "ga-firm" });
    expect(third.status).toBe(400);
    const { firmId: f2, cookie: c2 } = await createFirm("PeopleCapFirmTotal");
    for (let i = 0; i < 25; i++) await seed(f2, `ft${i}-${t}@example.com`, "georgia", `type${i}-firm`);
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

describe("same-state plus-alias -> 409 (documented boundary between the two identity notions)", () => {
  it("a+jane@ and a+bob@ cannot both hold the SAME state: the dedupe (folded identity) answers 409 before any seat logic; a different state is judged by seats", async () => {
    const { firmId, cookie } = await createFirm("PeopleCapSameStateAlias");
    const t = Date.now();
    await seed(firmId, `a+jane${t}@example.com`, "georgia", "ga-individual");
    const sameState = await post(cookie, { email: `a+bob${t}@example.com`, state_slug: "georgia", license_type_id: "ga-individual" });
    // The mailbox base 'a' is the same person to findActiveOrPending(), so this is a dedupe 409, NOT a 402.
    // Pinned on purpose: changing the dedupe to exact-email would reopen the plus-alias seat bypass.
    expect(sameState.status).toBe(409);
    const otherState = await post(cookie, { email: `a+bob${t}@example.com`, state_slug: "illinois", license_type_id: "il-individual" });
    expect(otherState.status).toBe(201); // exact-email identity: a different person for seats (2 of 3 used)
    expect((await listBody(cookie)).seat_count).toBe(2);
  });
});

describe("scheduler over-cap notices count people (runTrialEndingAlertPass)", () => {
  type Sent = { to: string; subject: string; text: string };
  async function runPass(sent: Sent[]): Promise<void> {
    await runTrialEndingAlertPass({ ...env, SEND_APPROVED_PASSES: "trialEndingAlert" } as never, {
      send: async (to, built) => {
        sent.push({ to, subject: built.subject, text: built.textBody });
        return true;
      },
    });
  }
  async function firmWithAdmin(name: string): Promise<{ firmId: string; admin: string }> {
    const { firmId } = await createFirm(name);
    const row = await env.DB.prepare("SELECT admin_email FROM firms WHERE id = ?1").bind(firmId).first<{ admin_email: string }>();
    return { firmId, admin: row!.admin_email };
  }

  it("trial-ending-soon: a multi-state firm UNDER the people cap gets no warning; a genuinely over-cap firm still does", async () => {
    const soon = new Date(Date.now() + 86_400_000).toISOString();
    const under = await firmWithAdmin("PeopleCapNoticeUnder");
    const over = await firmWithAdmin("PeopleCapNoticeOver");
    await env.DB.prepare("UPDATE firms SET trial_ends_at = ?1 WHERE id IN (?2, ?3)").bind(soon, under.firmId, over.firmId).run();
    const t = Date.now();
    // 3 people x 4 states = 12 lines (lines > cap 3, people == cap 3)
    for (const who of ["u1", "u2", "u3"]) for (const st of ["georgia", "alabama", "alaska", "arizona"]) await seed(under.firmId, `${who}-${t}@example.com`, st, `${st}-individual`);
    // 4 distinct people = genuinely over the cap
    for (const who of ["o1", "o2", "o3", "o4"]) await seed(over.firmId, `${who}-${t}@example.com`, "georgia", "ga-individual");
    const sent: Sent[] = [];
    await runPass(sent);
    expect(sent.filter((m) => m.to === under.admin)).toHaveLength(0);
    expect(sent.filter((m) => m.to === over.admin)).toHaveLength(1);
  });

  it("roster-paused notice counts staff as people, not lines", async () => {
    const f = await firmWithAdmin("PeopleCapNoticePaused");
    await env.DB.prepare("UPDATE firms SET trial_ends_at = ?1 WHERE id = ?2")
      .bind(new Date(Date.now() - 8 * 86_400_000).toISOString(), f.firmId)
      .run();
    const t = Date.now();
    const order = [["p1", "georgia"], ["p1", "alabama"], ["p2", "georgia"], ["p3", "georgia"], ["p4", "georgia"], ["p4", "alabama"], ["p5", "georgia"]] as const;
    for (let i = 0; i < order.length; i++) {
      const [who, st] = order[i]!;
      const id = await seed(f.firmId, `${who}-${t}@example.com`, st, `${st}-individual`);
      await env.DB.prepare("UPDATE subscribers SET created_at = ?1 WHERE id = ?2").bind(new Date(t + i).toISOString(), id).run();
    }
    const sent: Sent[] = [];
    await runPass(sent);
    const mine = sent.filter((m) => m.to === f.admin);
    expect(mine).toHaveLength(1);
    // 5 people, cap 3: p4 and p5 paused = 2 staff (4 lines), 3 active (p1,p2,p3)
    expect(mine[0]!.subject).toContain("2 staff paused");
    expect(mine[0]!.text).toContain("your 3 earliest-added staff");
  });
});

describe("SEAT-1: concurrent adds cannot both win the last seat (post-insert re-check)", () => {
  const mk = (id: string, email: string, typeId: string, t: number) => ({
    id,
    email,
    created_at: new Date(t).toISOString(),
    deadline_fields: JSON.stringify({ license_type_id: typeId }),
  });

  it("pure: of two NEW people past the cap, the later-ranked row is told to undo and the earlier stands (symmetric for both racers)", () => {
    const base = [mk("1", "a@x.com", "ga-individual", 1), mk("2", "b@x.com", "ga-individual", 2)];
    const c = mk("3", "c@x.com", "ga-individual", 3);
    const d = mk("4", "d@x.com", "ga-individual", 3); // same ms as c: id breaks the tie
    const rows = [...base, c, d];
    expect(addViolatesAfterInsert(rows, c, 3)).toEqual({ ok: true });
    expect(addViolatesAfterInsert(rows, d, 3)).toEqual({ ok: false, reason: "seat_cap" });
  });

  it("pure: an existing person's extra state line is never the 'new person'; ceiling boundary is exact (10th ok, 11th flagged)", () => {
    const rows = [mk("1", "a@x.com", "ga-individual", 1), mk("2", "b@x.com", "ga-individual", 2), mk("3", "c@x.com", "ga-individual", 3), mk("9", "a@x.com", "il-individual", 9)];
    expect(addViolatesAfterInsert(rows, rows[3]!, 3)).toEqual({ ok: true });
    const lines = Array.from({ length: 11 }, (_, i) => mk(`L${String(i).padStart(2, "0")}`, "p@x.com", `s${i}-individual`, 100 + i));
    expect(addViolatesAfterInsert(lines, lines[9]!, 3)).toEqual({ ok: true });
    expect(addViolatesAfterInsert(lines, lines[10]!, 3)).toEqual({ ok: false, reason: "person_line_ceiling" });
  });

  it("pure: a frozen over-cap roster is not retroactively flagged for an EXISTING person's new line", () => {
    const over = ["a", "b", "c", "d", "e"].map((n, i) => mk(String(i), `${n}@x.com`, "ga-individual", i + 1));
    const extra = mk("z", "e@x.com", "il-individual", 50);
    expect(addViolatesAfterInsert([...over, extra], extra, 3)).toEqual({ ok: true });
  });

  it("HTTP with FORCED interleave: both requests pass the gate read before either inserts -> exactly one survives", async () => {
    const { firmId, cookie } = await createFirm("PeopleCapRace");
    const t = Date.now();
    await seed(firmId, `r1-${t}@example.com`, "georgia", "ga-individual");
    await seed(firmId, `r2-${t}@example.com`, "georgia", "ga-individual");
    // Barrier: each request's FIRST roster read (the gate) waits until both have done it.
    let arrived = 0;
    let release!: () => void;
    const both = new Promise<void>((res) => (release = res));
    const wrapDb = (db: D1Database): D1Database => {
      let firstRosterRead = true;
      return new Proxy(db, {
        get(target, prop) {
          if (prop !== "prepare") return Reflect.get(target, prop).bind?.(target) ?? Reflect.get(target, prop);
          return (sql: string) => {
            const stmt = target.prepare(sql);
            const isRosterRead = /FROM subscribers\s+WHERE firm_id = \?1 AND NOT \(status/.test(sql);
            if (!isRosterRead) return stmt;
            return new Proxy(stmt, {
              get(st, p) {
                if (p !== "bind") return Reflect.get(st, p).bind(st);
                return (...args: unknown[]) => {
                  const bound = st.bind(...args);
                  return new Proxy(bound, {
                    get(bs, bp) {
                      if (bp !== "all") return Reflect.get(bs, bp).bind(bs);
                      return async () => {
                        if (firstRosterRead) {
                          firstRosterRead = false;
                          arrived += 1;
                          if (arrived === 2) release();
                          await both;
                        }
                        return bs.all();
                      };
                    },
                  });
                };
              },
            });
          };
        },
      });
    };
    const worker = (await import("../src/index")).default;
    const ctx = { waitUntil() {}, passThroughOnException() {}, props: {} } as unknown as ExecutionContext;
    const send = (who: string) =>
      worker.fetch(
        new Request("https://deadline-radar.com/firm/licenses", {
          method: "POST",
          headers: { "content-type": "application/json", "cf-connecting-ip": "203.0.113.201", Cookie: cookie },
          body: JSON.stringify({ email: `race-${who}-${t}@example.com`, state_slug: "georgia", license_type_id: "ga-individual" }),
        }),
        { ...env, DB: wrapDb(env.DB) } as never,
        ctx
      );
    const [x, y] = await Promise.all([send("x"), send("y")]);
    expect(arrived).toBe(2); // both really sat at the gate together
    expect([x.status, y.status].sort()).toEqual([201, 402]);
    expect(await store.countFirmLicenses(env.DB, firmId)).toBe(3);
  });
});

describe("ceiling constants are pinned by literal value", () => {
  it("10 per person, 25 firm-entity total, 2 per firm type (changing one must be a deliberate, reviewed edit)", async () => {
    const m = await import("../src/seat_usage");
    expect([m.PER_PERSON_LINE_CEILING, m.FIRM_ENTITY_LINE_CEILING, m.FIRM_ENTITY_LINES_PER_TYPE]).toEqual([10, 25, 2]);
  });
});
