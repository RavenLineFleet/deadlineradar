/**
 * CPE certificate auto-fill (roadmap #96/#157/#115, 2026-10-13). See
 * store.ts's "Document storage" section and index.ts's
 * handleDocumentExtract()/extractCpeCertificateFields() for the full
 * design. This endpoint is a strict superset of POST /firm/licenses/:id/
 * documents (same upload mechanics, see documents.spec.ts for those) plus
 * an optional Claude API call -- these tests focus on what's NEW here:
 * the extraction response itself (parseCpeExtractionResponseText, unit-
 * tested directly with crafted text, no network involved) and the
 * endpoint's own auth/entitlement/rate-limit checks and its dark-by-
 * default behavior with ANTHROPIC_API_KEY unset (the real prod state
 * until Devin authorizes the live key -- this codebase has no fetch-
 * injection precedent for mocking the actual Claude call itself, same gap
 * documented in slack-integration.spec.ts for the Slack token exchange).
 */
import { env, SELF } from "cloudflare:test";
import { describe, expect, it } from "vitest";
import * as store from "../src/store";
import { parseCpeExtractionResponseText } from "../src/validation";

const BASE = "https://deadline-radar.com";

async function createFirmWithSession(name: string, adminEmail: string): Promise<{ firmId: string; cookie: string }> {
  const firm = await store.createFirm(env.DB, { name, adminEmail });
  await env.DB.prepare("UPDATE firms SET created_at = '2020-01-01T00:00:00Z' WHERE id = ?1").bind(firm.id).run();
  const { rawSessionToken } = await store.createSession(env.DB, firm.id);
  return { firmId: firm.id, cookie: `dr_firm_session=${rawSessionToken}` };
}

async function addStaff(cookie: string, fields: Record<string, string>): Promise<{ id: string; email: string }> {
  const resp = await SELF.fetch(`${BASE}/firm/licenses`, {
    method: "POST",
    headers: { "content-type": "application/json", Cookie: cookie },
    body: JSON.stringify(fields),
  });
  expect(resp.status).toBe(201);
  return (await resp.json()) as { id: string; email: string };
}

function pdfBytes(size = 1024): Uint8Array {
  const bytes = new Uint8Array(size);
  bytes.set([0x25, 0x50, 0x44, 0x46, 0x2d], 0);
  for (let i = 5; i < size; i++) bytes[i] = i % 256;
  return bytes;
}

function extractForm(bytes: Uint8Array, filename = "cert.pdf", contentType = "application/pdf"): FormData {
  const fd = new FormData();
  fd.append("file", new File([bytes], filename, { type: contentType }));
  return fd;
}

async function extractDocument(cookie: string, subscriberId: string, bytes: Uint8Array): Promise<Response> {
  return SELF.fetch(`${BASE}/firm/licenses/${subscriberId}/documents/extract`, {
    method: "POST",
    headers: { Cookie: cookie },
    body: extractForm(bytes),
  });
}

describe("parseCpeExtractionResponseText() -- the untrusted-input half", () => {
  it("accepts a clean, fully-valid response", () => {
    const draft = parseCpeExtractionResponseText(
      '{"entry_date": "2026-09-15", "hours": 2.5, "category": "ethics", "description": "AICPA Ethics Update"}'
    );
    expect(draft).toEqual({ entry_date: "2026-09-15", hours: 2.5, category: "ethics", description: "AICPA Ethics Update" });
  });

  it("accepts hours given as a numeric string", () => {
    const draft = parseCpeExtractionResponseText('{"entry_date": "2026-09-15", "hours": "4", "category": "general", "description": null}');
    expect(draft.hours).toBe(4);
  });

  it("extracts JSON from a fenced/markdown-wrapped response", () => {
    const text = '```json\n{"entry_date": "2026-09-15", "hours": 2, "category": "general", "description": null}\n```';
    const draft = parseCpeExtractionResponseText(text);
    expect(draft.entry_date).toBe("2026-09-15");
    expect(draft.hours).toBe(2);
  });

  it("extracts JSON from a response with leading prose", () => {
    const text = 'Here is the extracted data: {"entry_date": "2026-09-15", "hours": 2, "category": "general", "description": null}';
    const draft = parseCpeExtractionResponseText(text);
    expect(draft.hours).toBe(2);
  });

  it("returns an all-null draft for non-JSON text", () => {
    expect(parseCpeExtractionResponseText("I could not read this certificate clearly.")).toEqual({
      entry_date: null,
      hours: null,
      category: null,
      description: null,
    });
  });

  it("returns an all-null draft for an empty string", () => {
    expect(parseCpeExtractionResponseText("")).toEqual({ entry_date: null, hours: null, category: null, description: null });
  });

  it("returns an all-null draft for malformed JSON inside braces", () => {
    expect(parseCpeExtractionResponseText("{not: valid, json}")).toEqual({
      entry_date: null,
      hours: null,
      category: null,
      description: null,
    });
  });

  it("returns an all-null draft for a brace-free array (no { } for the regex to match at all)", () => {
    expect(parseCpeExtractionResponseText("[1, 2, 3]")).toEqual({ entry_date: null, hours: null, category: null, description: null });
  });

  // AuditLab + SecurityLab (independently, 2026-10-14): the test above
  // passes for the WRONG reason -- it has no braces, so the brace-matching
  // regex fails before the `typeof parsed !== "object"` guard is ever
  // reached, not because that guard rejected an array. This test exercises
  // the guard's REAL behavior: a top-level array containing an object
  // never reaches the guard as an array either -- the greedy
  // `/\{[\s\S]*\}/` lifts just the innermost brace-delimited span out of
  // the array wrapper, so JSON.parse sees (and the guard receives) the
  // INNER OBJECT, not the array. SecurityLab executed this exact input and
  // confirmed it returns the lifted object's own validated fields, not an
  // empty draft. Not a vulnerability either way -- a lifted field still
  // clears every validator before it's returned -- but the array-shaped
  // `typeof` guard is effectively unreachable given this regex strategy,
  // and this test is what would catch a future regex change that made it
  // reachable (or made this stop being true).
  it("a top-level array WRAPPING an object lifts the inner object out, rather than being rejected as an array", () => {
    const draft = parseCpeExtractionResponseText('[{"hours":8}]');
    expect(draft).toEqual({ entry_date: null, hours: 8, category: null, description: null });
  });

  it("rejects an invalid calendar date field (null, not coerced)", () => {
    const draft = parseCpeExtractionResponseText('{"entry_date": "2026-02-30", "hours": 2, "category": "general", "description": null}');
    expect(draft.entry_date).toBeNull();
    expect(draft.hours).toBe(2); // the one bad field doesn't poison the others
  });

  it("rejects a non-ISO date string", () => {
    const draft = parseCpeExtractionResponseText('{"entry_date": "September 15, 2026", "hours": 2, "category": "general"}');
    expect(draft.entry_date).toBeNull();
  });

  it("rejects an out-of-range hours value instead of silently capping it", () => {
    const draft = parseCpeExtractionResponseText('{"entry_date": "2026-09-15", "hours": 500, "category": "general"}');
    expect(draft.hours).toBeNull();
  });

  it("rejects a zero or negative hours value", () => {
    expect(parseCpeExtractionResponseText('{"hours": 0}').hours).toBeNull();
    expect(parseCpeExtractionResponseText('{"hours": -3}').hours).toBeNull();
  });

  it("rejects a category outside the fixed general/ethics/other set", () => {
    const draft = parseCpeExtractionResponseText('{"entry_date": "2026-09-15", "hours": 2, "category": "accounting_and_auditing"}');
    expect(draft.category).toBeNull();
  });

  it("treats a missing field as null rather than erroring", () => {
    const draft = parseCpeExtractionResponseText('{"entry_date": "2026-09-15"}');
    expect(draft).toEqual({ entry_date: "2026-09-15", hours: null, category: null, description: null });
  });

  it("caps an oversized description through the same sanitizer manual entry uses", () => {
    const longText = "A".repeat(500);
    const draft = parseCpeExtractionResponseText(`{"description": "${longText}"}`);
    expect(draft.description).not.toBeNull();
    expect((draft.description as string).length).toBeLessThanOrEqual(200);
  });

  it("control characters in the description are stripped by the shared sanitizer", () => {
    const draft = parseCpeExtractionResponseText('{"description": "Ethics\\u0000Course"}');
    expect(draft.description).toBe("EthicsCourse");
  });
});

describe("POST /firm/licenses/:id/documents/extract", () => {
  it("401s with no session", async () => {
    const resp = await extractDocument("", "whatever", pdfBytes());
    expect(resp.status).toBe(401);
  });

  it("404s for a subscriber that doesn't belong to this firm", async () => {
    const { cookie: cookieA } = await createFirmWithSession("Extract Firm A", `extracta-${Date.now()}@example.com`);
    const { cookie: cookieB } = await createFirmWithSession("Extract Firm B", `extractb-${Date.now()}@example.com`);
    const staffB = await addStaff(cookieB, {
      staff_label: "B Staff",
      email: `extract-bstaff-${Date.now()}@example.com`,
      state_slug: "georgia",
      license_type_id: "ga-individual",
    });
    const resp = await extractDocument(cookieA, staffB.id, pdfBytes());
    expect(resp.status).toBe(404);
  });

  it("rejects an unsupported file type", async () => {
    const { cookie } = await createFirmWithSession("Extract Firm Type", `extract-type-${Date.now()}@example.com`);
    const staff = await addStaff(cookie, {
      staff_label: "Type Staff",
      email: `extract-typestaff-${Date.now()}@example.com`,
      state_slug: "georgia",
      license_type_id: "ga-individual",
    });
    const fd = new FormData();
    fd.append("file", new File([new Uint8Array([1, 2, 3])], "cert.exe", { type: "application/x-msdownload" }));
    const resp = await SELF.fetch(`${BASE}/firm/licenses/${staff.id}/documents/extract`, { method: "POST", headers: { Cookie: cookie }, body: fd });
    expect(resp.status).toBe(400);
  });

  it("rejects an oversized file", async () => {
    const { cookie } = await createFirmWithSession("Extract Firm Size", `extract-size-${Date.now()}@example.com`);
    const staff = await addStaff(cookie, {
      staff_label: "Size Staff",
      email: `extract-sizestaff-${Date.now()}@example.com`,
      state_slug: "georgia",
      license_type_id: "ga-individual",
    });
    const resp = await extractDocument(cookie, staff.id, pdfBytes(3 * 1024 * 1024));
    expect(resp.status).toBe(400);
  });

  it("a demo-locked firm never gets extraction, even with a key configured -- real spend an anonymous demo visitor could trigger repeatedly", async () => {
    // Mutating the test env's own binding directly (rather than relying on
    // the suite-wide unset default) to prove the demo_locked short-circuit
    // specifically, independent of the "no key configured" path -- without
    // this, a regression that checked demo_locked AFTER the key-presence
    // check (instead of combined in one condition) would pass every other
    // test here and still leak extraction to the shared public demo.
    (env as unknown as { ANTHROPIC_API_KEY?: string }).ANTHROPIC_API_KEY = "test-fake-key-never-sent";
    try {
      const { firmId, cookie } = await createFirmWithSession("Extract Firm Demo", `extract-demo-${Date.now()}@example.com`);
      await env.DB.prepare("UPDATE firms SET demo_locked = 1 WHERE id = ?1").bind(firmId).run();
      const staff = await addStaff(cookie, {
        staff_label: "Demo Staff",
        email: `extract-demostaff-${Date.now()}@example.com`,
        state_slug: "georgia",
        license_type_id: "ga-individual",
      });
      const resp = await extractDocument(cookie, staff.id, pdfBytes());
      expect(resp.status).toBe(201);
      const body = (await resp.json()) as { extraction_available: boolean; draft: unknown };
      expect(body.extraction_available).toBe(false);
      expect(body.draft).toBeNull();
    } finally {
      (env as unknown as { ANTHROPIC_API_KEY?: string }).ANTHROPIC_API_KEY = undefined;
    }
  });

  it("with no ANTHROPIC_API_KEY configured (the real prod default), the upload still succeeds and extraction is reported unavailable", async () => {
    expect(env.ANTHROPIC_API_KEY).toBeUndefined();
    const { cookie } = await createFirmWithSession("Extract Firm Dark", `extract-dark-${Date.now()}@example.com`);
    const staff = await addStaff(cookie, {
      staff_label: "Dark Staff",
      email: `extract-darkstaff-${Date.now()}@example.com`,
      state_slug: "georgia",
      license_type_id: "ga-individual",
    });
    const resp = await extractDocument(cookie, staff.id, pdfBytes());
    expect(resp.status).toBe(201);
    const body = (await resp.json()) as { document: { id: string; kind: string }; extraction_available: boolean; draft: unknown };
    expect(body.extraction_available).toBe(false);
    expect(body.draft).toBeNull();
    expect(body.document.id).toBeTruthy();

    // The document itself is a real, ordinary 'cpe' document -- identical
    // to what plain upload would have created, confirming this endpoint
    // is a strict superset, not a separate storage path.
    const listResp = await SELF.fetch(`${BASE}/firm/licenses/${staff.id}/documents`, { headers: { Cookie: cookie } });
    const listed = (await listResp.json()) as { documents: Array<{ id: string; kind: string }> };
    expect(listed.documents.some((d) => d.id === body.document.id && d.kind === "cpe")).toBe(true);
  });

  it("enforces its own tighter rate limit independently of the plain-upload limit", async () => {
    const { firmId, cookie } = await createFirmWithSession("Extract Firm RL", `extract-rl-${Date.now()}@example.com`);
    const staff = await addStaff(cookie, {
      staff_label: "RL Staff",
      email: `extract-rlstaff-${Date.now()}@example.com`,
      state_slug: "georgia",
      license_type_id: "ga-individual",
    });
    const now = Math.floor(Date.now() / 1000);
    // RATE_LIMIT_FIRM_DOCUMENT_EXTRACT's cap is 20/day -- pre-seed 20 hits
    // so the very next real call is the one that trips it, rather than
    // looping 20 real uploads (which would also work, just slower).
    for (let i = 0; i < 20; i++) {
      await env.DB.prepare("INSERT INTO rate_limit_hits (ip, bucket, ts) VALUES (?1, ?2, ?3)").bind(firmId, "firm_document_extract", now - i).run();
    }
    const resp = await extractDocument(cookie, staff.id, pdfBytes());
    expect(resp.status).toBe(429);
  });

  it("gated for a post-cutover free firm, same as plain upload", async () => {
    const future = new Date(Date.now() + 86_400_000).toISOString();
    const firm = await store.createFirm(env.DB, { name: "Extract Gate Firm", adminEmail: `extract-gate-${Date.now()}@example.com` });
    await env.DB.prepare("UPDATE firms SET plan_tier = 'free', created_at = ?1 WHERE id = ?2").bind(future, firm.id).run();
    await env.DB.prepare("UPDATE firms SET trial_ends_at = NULL WHERE id = ?1").bind(firm.id).run();
    const { rawSessionToken } = await store.createSession(env.DB, firm.id);
    const cookie = `dr_firm_session=${rawSessionToken}`;
    const staff = await addStaff(cookie, {
      staff_label: "Gated Staff",
      email: `extract-gatestaff-${Date.now()}@example.com`,
      state_slug: "georgia",
      license_type_id: "ga-individual",
    });
    const resp = await extractDocument(cookie, staff.id, pdfBytes());
    expect(resp.status).toBe(403);
  });
});
