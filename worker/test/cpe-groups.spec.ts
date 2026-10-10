/**
 * "Log a course once, apply it to the states I pick" (roadmap #344, migration 0091).
 * One POST /firm/cpe with `also` writes one ordinary cpe_entries row per chosen line
 * of the SAME person, sharing a group_id, atomically. Every server-side rule has a
 * paired positive control on the same fixture so a refusal can't pass vacuously:
 *   - firm binding (cross-firm id -> 404, nothing written in either firm)
 *   - same-person binding by exact normalized email (other person / plus-alias -> 400)
 *   - firm-entity (-firm) lines take no CPE, removed lines are not targets
 *   - duplicate / too-many targets refused, nothing written
 *   - "remove all linked" is firm-bound and scoped to one group
 */
import { env, SELF } from "cloudflare:test";
import { describe, expect, it } from "vitest";
import * as store from "../src/store";

async function createFirm(name: string): Promise<{ firmId: string; cookie: string }> {
  const firm = await store.createFirm(env.DB, { name, adminEmail: `${name.replace(/\W/g, "")}-${Date.now()}@example.com` });
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

function today(): string {
  return new Date().toISOString().slice(0, 10);
}

async function postCpe(cookie: string, body: Record<string, unknown>): Promise<Response> {
  return SELF.fetch("https://deadline-radar.com/firm/cpe", {
    method: "POST",
    headers: { "content-type": "application/json", "cf-connecting-ip": "203.0.113.210", Cookie: cookie },
    body: JSON.stringify(body),
  });
}

async function delCpe(cookie: string, id: string, scope?: string): Promise<Response> {
  return SELF.fetch(`https://deadline-radar.com/firm/cpe/${id}${scope ? `?scope=${scope}` : ""}`, {
    method: "DELETE",
    headers: { "cf-connecting-ip": "203.0.113.210", Cookie: cookie },
  });
}

async function liveEntries(firmId: string): Promise<{ id: string; subscriber_id: string; group_id: string | null; hours: number; category: string }[]> {
  const { results } = await env.DB.prepare(
    "SELECT id, subscriber_id, group_id, hours, category FROM cpe_entries WHERE firm_id = ?1 AND deleted_at IS NULL"
  )
    .bind(firmId)
    .all<{ id: string; subscriber_id: string; group_id: string | null; hours: number; category: string }>();
  return results;
}

const base = (subscriber_id: string) => ({ subscriber_id, entry_date: today(), hours: "4", category: "general", description: "AICPA update" });

describe("POST /firm/cpe with `also` -- log once, apply to several of the SAME person's lines", () => {
  it("writes one row per chosen line sharing a group_id; defaults copy hours/category, overrides apply per line", async () => {
    const { firmId, cookie } = await createFirm("CpeGroupHappy");
    const p = `multi-${Date.now()}@example.com`;
    const ga = await seed(firmId, p, "georgia", "ga-individual");
    const il = await seed(firmId, p, "illinois", "il-individual");
    const al = await seed(firmId, p, "alabama", "al-individual");
    const r = await postCpe(cookie, {
      ...base(ga),
      also: [{ subscriber_id: il }, { subscriber_id: al, hours: "2", category: "ethics" }],
    });
    expect(r.status).toBe(201);
    const body = (await r.json()) as { id: string; group_id: string; linked_entries: { subscriber_id: string; group_id: string; hours: number; category: string }[] };
    expect(body.linked_entries.length).toBe(2);
    const rows = await liveEntries(firmId);
    expect(rows.length).toBe(3);
    expect(new Set(rows.map((x) => x.group_id)).size).toBe(1);
    expect(rows[0]!.group_id).toBe(body.group_id);
    const byLine = Object.fromEntries(rows.map((x) => [x.subscriber_id, x]));
    expect([byLine[ga]!.hours, byLine[ga]!.category]).toEqual([4, "general"]);
    expect([byLine[il]!.hours, byLine[il]!.category]).toEqual([4, "general"]); // defaults copied
    expect([byLine[al]!.hours, byLine[al]!.category]).toEqual([2, "ethics"]); // per-line override
    // GET exposes group_id so the dashboard can show the link
    const list = await SELF.fetch("https://deadline-radar.com/firm/cpe", { headers: { "cf-connecting-ip": "203.0.113.210", Cookie: cookie } });
    const listed = (await list.json()) as { entries: { group_id: string | null }[] };
    expect(listed.entries.filter((e) => e.group_id === body.group_id).length).toBe(3);
  });

  it("a plain entry (no `also`) is unchanged: one row, group_id null", async () => {
    const { firmId, cookie } = await createFirm("CpeGroupPlain");
    const ga = await seed(firmId, `solo-${Date.now()}@example.com`, "georgia", "ga-individual");
    expect((await postCpe(cookie, base(ga))).status).toBe(201);
    const rows = await liveEntries(firmId);
    expect(rows.length).toBe(1);
    expect(rows[0]!.group_id).toBeNull();
  });

  it("CROSS-PERSON: another person's line in the same firm -> 400 and NOTHING is written (control: the same person's line works)", async () => {
    const { firmId, cookie } = await createFirm("CpeGroupOtherPerson");
    const t = Date.now();
    const a = await seed(firmId, `a-${t}@example.com`, "georgia", "ga-individual");
    const aIl = await seed(firmId, `a-${t}@example.com`, "illinois", "il-individual");
    const b = await seed(firmId, `b-${t}@example.com`, "alabama", "al-individual");
    const bad = await postCpe(cookie, { ...base(a), also: [{ subscriber_id: aIl }, { subscriber_id: b }] });
    expect(bad.status).toBe(400);
    expect((await liveEntries(firmId)).length).toBe(0); // atomic: even the valid sibling was not written
    const ok = await postCpe(cookie, { ...base(a), also: [{ subscriber_id: aIl }] });
    expect(ok.status).toBe(201);
    expect((await liveEntries(firmId)).length).toBe(2);
  });

  it("PLUS-ALIAS is a different person (exact-email identity, same as the seat count)", async () => {
    const { firmId, cookie } = await createFirm("CpeGroupAlias");
    const t = Date.now();
    const a = await seed(firmId, `a${t}@example.com`, "georgia", "ga-individual");
    const alias = await seed(firmId, `a+x${t}@example.com`, "illinois", "il-individual");
    expect((await postCpe(cookie, { ...base(a), also: [{ subscriber_id: alias }] })).status).toBe(400);
    expect((await liveEntries(firmId)).length).toBe(0);
  });

  it("CROSS-FIRM: a line id from another firm -> 404 and nothing is written in EITHER firm (control: own line works)", async () => {
    const { firmId, cookie } = await createFirm("CpeGroupFirmA");
    const other = await createFirm("CpeGroupFirmB");
    const p = `same-${Date.now()}@example.com`; // even the same email in another firm must not link
    const mine = await seed(firmId, p, "georgia", "ga-individual");
    const mineIl = await seed(firmId, p, "illinois", "il-individual");
    const theirs = await seed(other.firmId, p, "alabama", "al-individual");
    const bad = await postCpe(cookie, { ...base(mine), also: [{ subscriber_id: theirs }] });
    expect(bad.status).toBe(404);
    expect((await liveEntries(firmId)).length).toBe(0);
    expect((await liveEntries(other.firmId)).length).toBe(0);
    // primary from another firm is refused the same way
    expect((await postCpe(cookie, { ...base(theirs), also: [{ subscriber_id: mineIl }] })).status).toBe(404);
    expect((await postCpe(cookie, { ...base(mine), also: [{ subscriber_id: mineIl }] })).status).toBe(201);
  });

  it("firm-entity (-firm) lines and admin-removed lines are not valid targets (controls: a normal line works)", async () => {
    const { firmId, cookie } = await createFirm("CpeGroupTargets");
    const p = `t-${Date.now()}@example.com`;
    const ga = await seed(firmId, p, "georgia", "ga-individual");
    const firmLine = await seed(firmId, p, "illinois", "il-firm");
    const gone = await seed(firmId, p, "alabama", "al-individual");
    const fine = await seed(firmId, p, "alaska", "ak-individual");
    const del = await SELF.fetch(`https://deadline-radar.com/firm/licenses/${gone}`, {
      method: "DELETE",
      headers: { Cookie: cookie, "cf-connecting-ip": "203.0.113.210" },
    });
    expect(del.status).toBe(200);
    expect((await postCpe(cookie, { ...base(ga), also: [{ subscriber_id: firmLine }] })).status).toBe(400);
    expect((await postCpe(cookie, { ...base(ga), also: [{ subscriber_id: gone }] })).status).toBe(404);
    expect((await liveEntries(firmId)).length).toBe(0);
    expect((await postCpe(cookie, { ...base(ga), also: [{ subscriber_id: fine }] })).status).toBe(201);
  });

  it("a firm-entity (-firm) line cannot be the PRIMARY either (control: the same person's normal line as primary works)", async () => {
    const { firmId, cookie } = await createFirm("CpeGroupFirmPrimary");
    const p = `fp-${Date.now()}@example.com`;
    const firmLine = await seed(firmId, p, "illinois", "il-firm");
    const ga = await seed(firmId, p, "georgia", "ga-individual");
    const al = await seed(firmId, p, "alabama", "al-individual");
    expect((await postCpe(cookie, { ...base(firmLine), also: [{ subscriber_id: ga }] })).status).toBe(400);
    expect((await liveEntries(firmId)).length).toBe(0);
    expect((await postCpe(cookie, { ...base(ga), also: [{ subscriber_id: al }] })).status).toBe(201);
  });

  it("the group size limit IS the per-person line ceiling (one constant): 10 total lines, so 9 targets pass shape checks and 10 do not", async () => {
    const m = await import("../src/store");
    const u = await import("../src/seat_usage");
    expect(m.CPE_GROUP_MAX_LINES).toBe(u.PER_PERSON_LINE_CEILING);
    expect(m.CPE_GROUP_MAX_LINES).toBe(10);
  });

  it("duplicate targets, the primary listed again, an empty list and too many targets are refused with nothing written", async () => {
    const { firmId, cookie } = await createFirm("CpeGroupShape");
    const p = `s-${Date.now()}@example.com`;
    const ga = await seed(firmId, p, "georgia", "ga-individual");
    const il = await seed(firmId, p, "illinois", "il-individual");
    expect((await postCpe(cookie, { ...base(ga), also: [{ subscriber_id: il }, { subscriber_id: il }] })).status).toBe(400);
    expect((await postCpe(cookie, { ...base(ga), also: [{ subscriber_id: ga }] })).status).toBe(400);
    expect((await postCpe(cookie, { ...base(ga), also: [] })).status).toBe(400);
    expect((await postCpe(cookie, { ...base(ga), also: Array.from({ length: 10 }, (_, i) => ({ subscriber_id: `x${i}` })) })).status).toBe(400);
    expect((await postCpe(cookie, { ...base(ga), also: [{ subscriber_id: il, hours: "0" }] })).status).toBe(400);
    expect((await postCpe(cookie, { ...base(ga), also: [{ subscriber_id: il, category: "bogus" }] })).status).toBe(400);
    expect((await liveEntries(firmId)).length).toBe(0);
    expect((await postCpe(cookie, { ...base(ga), also: [{ subscriber_id: il }] })).status).toBe(201);
  });
});

describe("DELETE /firm/cpe/:id?scope=group -- remove all linked", () => {
  it("default removes ONE row; scope=group removes every row of that group and no other", async () => {
    const { firmId, cookie } = await createFirm("CpeGroupDelete");
    const p = `d-${Date.now()}@example.com`;
    const ga = await seed(firmId, p, "georgia", "ga-individual");
    const il = await seed(firmId, p, "illinois", "il-individual");
    const al = await seed(firmId, p, "alabama", "al-individual");
    const g1 = (await (await postCpe(cookie, { ...base(ga), also: [{ subscriber_id: il }, { subscriber_id: al }] })).json()) as { id: string; group_id: string };
    const g2 = (await (await postCpe(cookie, { ...base(ga), description: "second course", also: [{ subscriber_id: il }] })).json()) as { id: string; group_id: string };
    expect(g1.group_id).not.toBe(g2.group_id);
    expect((await liveEntries(firmId)).length).toBe(5);
    // single removal leaves the rest of its group
    expect((await delCpe(cookie, g1.id)).status).toBe(200);
    expect((await liveEntries(firmId)).length).toBe(4);
    // group removal takes the rest of g1 (2 rows) and leaves g2 alone
    const rows = await liveEntries(firmId);
    const g1Left = rows.find((x) => x.group_id === g1.group_id)!;
    const r = await delCpe(cookie, g1Left.id, "group");
    expect(r.status).toBe(200);
    expect(((await r.json()) as { removed_count: number }).removed_count).toBe(2);
    const after = await liveEntries(firmId);
    expect(after.length).toBe(2);
    expect(after.every((x) => x.group_id === g2.group_id)).toBe(true);
  });

  it("CROSS-FIRM: scope=group with another firm's entry id -> 404 and that firm's group is untouched", async () => {
    const a = await createFirm("CpeGroupDelA");
    const b = await createFirm("CpeGroupDelB");
    const p = `x-${Date.now()}@example.com`;
    const ga = await seed(b.firmId, p, "georgia", "ga-individual");
    const il = await seed(b.firmId, p, "illinois", "il-individual");
    const made = (await (await postCpe(b.cookie, { ...base(ga), also: [{ subscriber_id: il }] })).json()) as { id: string };
    const attempt = await delCpe(a.cookie, made.id, "group");
    expect(attempt.status).toBe(404);
    expect((await liveEntries(b.firmId)).length).toBe(2);
    expect((await delCpe(b.cookie, made.id, "group")).status).toBe(200); // control: the owner can
    expect((await liveEntries(b.firmId)).length).toBe(0);
  });

  it("scope=group on an entry with no group removes just that row", async () => {
    const { firmId, cookie } = await createFirm("CpeGroupDelPlain");
    const ga = await seed(firmId, `pl-${Date.now()}@example.com`, "georgia", "ga-individual");
    const one = (await (await postCpe(cookie, base(ga))).json()) as { id: string };
    await postCpe(cookie, base(ga));
    expect((await delCpe(cookie, one.id, "group")).status).toBe(200);
    expect((await liveEntries(firmId)).length).toBe(1);
  });
});

describe("rate-limit unit accounting (AuditLab CPE-6) -- pinned so a later tidy-up cannot change it silently", () => {
  async function units(firmId: string, bucket: string): Promise<number> {
    const r = await env.DB.prepare("SELECT COUNT(*) AS n FROM rate_limit_hits WHERE ip = ?1 AND bucket = ?2").bind(firmId, bucket).first<{ n: number }>();
    return r!.n;
  }

  it("create spends 1 unit per row written; group delete spends exactly 1 unit however many rows it removes", async () => {
    const { firmId, cookie } = await createFirm("CpeGroupUnits");
    const p = `u-${Date.now()}@example.com`;
    const ga = await seed(firmId, p, "georgia", "ga-individual");
    const il = await seed(firmId, p, "illinois", "il-individual");
    const al = await seed(firmId, p, "alabama", "al-individual");
    const g = (await (await postCpe(cookie, { ...base(ga), also: [{ subscriber_id: il }, { subscriber_id: al }] })).json()) as { id: string };
    expect(await units(firmId, "cpe_entry_create")).toBe(3);
    expect(await units(firmId, "cpe_entry_delete")).toBe(0);
    const r = await delCpe(cookie, g.id, "group");
    expect(((await r.json()) as { removed_count: number }).removed_count).toBe(3);
    expect(await units(firmId, "cpe_entry_delete")).toBe(1);
  });

  it("control: a second delete of the already-removed group 404s and its lookup writes no rows", async () => {
    const { firmId, cookie } = await createFirm("CpeGroupUnits2");
    const p = `u2-${Date.now()}@example.com`;
    const ga = await seed(firmId, p, "georgia", "ga-individual");
    const il = await seed(firmId, p, "illinois", "il-individual");
    const g = (await (await postCpe(cookie, { ...base(ga), also: [{ subscriber_id: il }] })).json()) as { id: string };
    expect((await delCpe(cookie, g.id, "group")).status).toBe(200);
    // compare the deleted_at VALUES, not a count of non-nulls: a count cannot see a re-write of already-deleted rows (AuditLab 16:48)
    const stamps = async () => (await env.DB.prepare("SELECT group_concat(deleted_at, '|') AS v FROM (SELECT deleted_at FROM cpe_entries WHERE firm_id = ?1 ORDER BY id)").bind(firmId).first<{ v: string }>())!.v;
    // BACKSTOP only (AuditLab 17:08): the 404 below is what catches dropping `deleted_at IS NULL`, because
    // removeCpeEntryGroup returns the UPDATE's own changes. This value compare guards a future decoupling of
    // return value from write, and only works while the two writes land in different ms -- hence the format pin.
    const before = await stamps();
    expect(before).toMatch(/^\d{4}-\d\d-\d\dT[\d:]+\.\d{3}Z\|\d{4}-\d\d-\d\dT[\d:]+\.\d{3}Z$/);
    expect((await delCpe(cookie, g.id, "group")).status).toBe(404);
    expect(await stamps()).toBe(before);
  });
});
