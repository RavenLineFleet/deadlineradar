/**
 * Task #7 (2026-08-06): operator-managed email/domain blocklist. Covers
 * store.isEmailBlocklisted() directly plus three of its call sites (public
 * /subscribe, /firm/signup, and authenticated firm staff-roster adds); it has
 * 8 production call sites as of SEC-42 (2026-10-24).
 */
import { env, SELF } from "cloudflare:test";
import { describe, expect, it } from "vitest";
import * as store from "../src/store";

function form(fields: Record<string, string>): string {
  return new URLSearchParams(fields).toString();
}

async function postSubscribe(fields: Record<string, string>, ip = "203.0.113.50"): Promise<Response> {
  return SELF.fetch("https://deadline-radar.com/subscribe", {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded", "cf-connecting-ip": ip },
    body: form({ hp_website: "", ...fields }),
  });
}

async function postFirmSignup(fields: Record<string, string>, ip = "203.0.113.51"): Promise<Response> {
  return SELF.fetch("https://deadline-radar.com/firm/signup", {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded", "cf-connecting-ip": ip },
    body: form({ hp_website: "", ...fields }),
  });
}

async function createFirmWithSession(name: string, adminEmail: string): Promise<{ firmId: string; cookie: string }> {
  const firm = await store.createFirm(env.DB, { name, adminEmail });
  const { rawSessionToken } = await store.createSession(env.DB, firm.id);
  return { firmId: firm.id, cookie: `dr_firm_session=${rawSessionToken}` };
}

async function postFirmLicense(cookie: string, body: Record<string, string>): Promise<Response> {
  return SELF.fetch("https://deadline-radar.com/firm/licenses", {
    method: "POST",
    headers: { "content-type": "application/json", "cf-connecting-ip": "203.0.113.52", Cookie: cookie },
    body: JSON.stringify(body),
  });
}

async function addBlocklistEntry(pattern: string, patternType: "email" | "domain"): Promise<void> {
  await env.DB.prepare(
    `INSERT INTO signup_blocklist (id, pattern, pattern_type, note, created_at) VALUES (?1,?2,?3,?4,?5)`
  )
    .bind(crypto.randomUUID(), pattern, patternType, "test", new Date().toISOString())
    .run();
}

describe("store.isEmailBlocklisted", () => {
  it("matches an exact blocked email (case-insensitively)", async () => {
    const email = `exact-${Date.now()}@blocked-example.com`;
    await addBlocklistEntry(email.toLowerCase(), "email");
    expect(await store.isEmailBlocklisted(env.DB, email.toUpperCase())).toBe(true);
  });

  it("matches a blocked domain, and a real subdomain of it, but not an unrelated address", async () => {
    const domain = `abuse-${Date.now()}.example.com`;
    await addBlocklistEntry(domain, "domain");
    expect(await store.isEmailBlocklisted(env.DB, `person@${domain}`)).toBe(true);
    expect(await store.isEmailBlocklisted(env.DB, `person@mail.${domain}`)).toBe(true);
    expect(await store.isEmailBlocklisted(env.DB, `person@not-${domain}`)).toBe(false);
    expect(await store.isEmailBlocklisted(env.DB, `person@totally-unrelated.example`)).toBe(false);
  });

  // SEC-42 site 3 (2026-10-24): an email-pattern entry must also catch the
  // entry's +tag/dot aliases -- the exact match alone let abuser+1@ through.
  it("SEC-42: matches +tag/dot aliases of a blocked email, but not a different local part", async () => {
    const local = `aliasblock${Date.now()}`;
    await addBlocklistEntry(`${local}@alias-example.com`, "email");
    expect(await store.isEmailBlocklisted(env.DB, `${local}+1@alias-example.com`)).toBe(true);
    expect(await store.isEmailBlocklisted(env.DB, `alias.block${local.slice(10)}@alias-example.com`)).toBe(true);
    expect(await store.isEmailBlocklisted(env.DB, `${local.toUpperCase()}+X@ALIAS-EXAMPLE.COM`)).toBe(true);
    expect(await store.isEmailBlocklisted(env.DB, `${local}x@alias-example.com`)).toBe(false);
    expect(await store.isEmailBlocklisted(env.DB, `${local}@other-alias-example.com`)).toBe(false);
  });

  // SEC-42 follow-up (SecurityLab advisory): the arms above store a bare
  // pattern and probe an alias, so only the email-side fold is pinned. Store
  // the tagged/dotted form (what an abuse report usually carries) and probe
  // the bare inbox -- fails if the pattern side is compared unfolded.
  it("SEC-42: a tagged/dotted stored pattern blocks the bare inbox", async () => {
    const local = `taggedblock${Date.now()}`;
    await addBlocklistEntry(`${local}+spam@alias-example.com`, "email");
    expect(await store.isEmailBlocklisted(env.DB, `${local}@alias-example.com`)).toBe(true);
    await addBlocklistEntry(`d.${local}@alias-example.com`, "email");
    expect(await store.isEmailBlocklisted(env.DB, `d${local}@alias-example.com`)).toBe(true);
  });

  it("returns false for an address with no blocklist entries", async () => {
    expect(await store.isEmailBlocklisted(env.DB, `clean-${Date.now()}@example.com`)).toBe(false);
  });
});

describe("blocklist enforcement across the three signup paths", () => {
  it("blocks POST /subscribe for a blocklisted exact email", async () => {
    const email = `sub-blocked-${Date.now()}@example.com`;
    await addBlocklistEntry(email, "email");
    const resp = await postSubscribe({ email, state: "georgia", license_type_id: "ga-individual" });
    expect(resp.status).toBe(400);
    const row = await env.DB.prepare("SELECT * FROM subscribers WHERE email = ?1").bind(email).first();
    expect(row).toBeNull();
  });

  it("blocks POST /subscribe for an address on a blocklisted domain", async () => {
    const domain = `sub-domain-${Date.now()}.example.com`;
    await addBlocklistEntry(domain, "domain");
    const resp = await postSubscribe({ email: `anyone@${domain}`, state: "georgia", license_type_id: "ga-individual" });
    expect(resp.status).toBe(400);
  });

  it("blocks POST /firm/signup for a blocklisted admin email", async () => {
    const email = `firmsignup-blocked-${Date.now()}@example.com`;
    await addBlocklistEntry(email, "email");
    const resp = await postFirmSignup({ admin_email: email, name: "Blocked Firm LLC" });
    expect(resp.status).toBe(400);
    const row = await env.DB.prepare("SELECT * FROM firms WHERE admin_email = ?1").bind(email).first();
    expect(row).toBeNull();
  });

  it("blocks a firm admin adding a blocklisted address to their own staff roster", async () => {
    const staffEmail = `staff-blocked-${Date.now()}@example.com`;
    await addBlocklistEntry(staffEmail, "email");
    const { cookie } = await createFirmWithSession("Roster Block Test LLC", `owner-${Date.now()}@example.com`);
    const resp = await postFirmLicense(cookie, {
      staff_label: "Blocked Staffer",
      email: staffEmail,
      state_slug: "georgia",
      license_type_id: "ga-individual",
    });
    expect(resp.status).toBe(400);
  });

  it("does not block an address that has no blocklist entry", async () => {
    const resp = await postSubscribe({
      email: `unblocked-${Date.now()}@example.com`,
      state: "georgia",
      license_type_id: "ga-individual",
    });
    expect(resp.status).toBe(200);
  });
});

// AuditLab BLOCKLIST-1 (MEDIUM, 2026-08-06 -> fixed 2026-08-07): the
// blocklist was enforced on the three SIGNUP paths but not on the two
// routes that accept a NEW address for an EXISTING record --
// /firm/change-email (AuditLab's finding) and PATCH /firm/licenses/:id's
// email edit (found by grepping for the same defining behavior).
describe("BLOCKLIST-1: existing-record email changes respect the blocklist too", () => {
  it("POST /firm/change-email to a blocklisted address is refused", async () => {
    const blocked = `changeemail-blocked-${Date.now()}@example.com`;
    await addBlocklistEntry(blocked, "email");
    const { cookie } = await createFirmWithSession("ChangeEmail Block Firm", `ce-owner-${Date.now()}@example.com`);
    const resp = await SELF.fetch("https://deadline-radar.com/firm/change-email", {
      method: "POST",
      headers: { "content-type": "application/json", "cf-connecting-ip": "203.0.113.53", Cookie: cookie, Origin: "https://deadline-radar.com" },
      body: JSON.stringify({ new_email: blocked, current_password: "" }),
    });
    expect(resp.status).toBe(400);
    const body = (await resp.json()) as { error: string };
    expect(body.error).toContain("not able to use that address");
  });

  it("PATCH /firm/licenses/:id email edit onto a blocklisted address is refused", async () => {
    const blocked = `patch-blocked-${Date.now()}@example.com`;
    await addBlocklistEntry(blocked, "email");
    const { cookie } = await createFirmWithSession("Patch Block Firm", `pb-owner-${Date.now()}@example.com`);
    const createResp = await postFirmLicense(cookie, {
      staff_label: "Patch Target",
      email: `patch-ok-${Date.now()}@example.com`,
      state_slug: "georgia",
      license_type_id: "ga-individual",
    });
    const created = (await createResp.json()) as { id: string };
    const resp = await SELF.fetch(`https://deadline-radar.com/firm/licenses/${created.id}`, {
      method: "PATCH",
      headers: { "content-type": "application/json", "cf-connecting-ip": "203.0.113.54", Cookie: cookie, Origin: "https://deadline-radar.com" },
      body: JSON.stringify({ email: blocked }),
    });
    expect(resp.status).toBe(400);
  });

  it("an unrelated PATCH on a row whose address was blocked AFTER it was added still succeeds", async () => {
    const laterBlocked = `later-blocked-${Date.now()}@example.com`;
    const { cookie } = await createFirmWithSession("Later Block Firm", `lb-owner-${Date.now()}@example.com`);
    const createResp = await postFirmLicense(cookie, {
      staff_label: "Grandfathered",
      email: laterBlocked,
      state_slug: "georgia",
      license_type_id: "ga-individual",
    });
    const created = (await createResp.json()) as { id: string };
    // Operator blocks the address only after it's already on the roster.
    await addBlocklistEntry(laterBlocked, "email");
    // The real edit modal always re-sends the row's email alongside any
    // other field -- so this sends the UNCHANGED (now-blocked) address
    // back, exercising the normalizeEmail same-address guard, not just
    // the omitted-field path.
    const resp = await SELF.fetch(`https://deadline-radar.com/firm/licenses/${created.id}`, {
      method: "PATCH",
      headers: { "content-type": "application/json", "cf-connecting-ip": "203.0.113.55", Cookie: cookie, Origin: "https://deadline-radar.com" },
      body: JSON.stringify({ staff_label: "Renamed, Address Untouched", email: laterBlocked }),
    });
    expect(resp.status).toBe(200);
    const body = (await resp.json()) as { staff_label: string };
    expect(body.staff_label).toBe("Renamed, Address Untouched");
  });
});
