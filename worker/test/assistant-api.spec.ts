/**
 * /assistant/* -- read-only lookups for orchestrator's DeadlineRadar chat
 * assistant droplet (2026-08-27). Two things this file proves:
 *   1. Each endpoint returns real data reused from the same datasets every
 *      other page on this site already sources from, correctly shaped and
 *      filtered by the query params.
 *   2. The LEAK-2 discipline actually holds here: no response anywhere
 *      contains `verification_history` or a `computation.note`-shaped leak
 *      -- checked by string search over the raw JSON body, not just by
 *      trusting the allowlist code looks right.
 */
import { env, SELF } from "cloudflare:test";
import { describe, expect, it } from "vitest";

const BASE = "https://deadline-radar.com";

async function getAssistant(path: string, ip = "203.0.113.50"): Promise<Response> {
  return SELF.fetch(`${BASE}${path}`, { headers: { "cf-connecting-ip": ip } });
}

function assertNoLeakedFields(rawBody: string): void {
  expect(rawBody).not.toContain("verification_history");
  // Florida's own original leak text -- if this ever appears in a response
  // body again, computation.note (or something shaped like it) leaked.
  expect(rawBody).not.toMatch(/renewal_month\/renewal_day/);
}

describe("GET /assistant/deadline", () => {
  it("returns Texas's individual record with the expected safe fields", async () => {
    const resp = await getAssistant("/api/assistant/deadline?state=texas&license_type=individual");
    expect(resp.status).toBe(200);
    const rawBody = await resp.text();
    assertNoLeakedFields(rawBody);
    const body = JSON.parse(rawBody) as { records: { state_slug: string; license_type: string; cycle_description: string }[] };
    expect(body.records).toHaveLength(1);
    expect(body.records[0]?.state_slug).toBe("texas");
    expect(body.records[0]?.license_type).toBe("individual");
    expect(body.records[0]?.cycle_description.length).toBeGreaterThan(0);
  });

  it("omitting license_type returns every record DeadlineRadar tracks for that state", async () => {
    const resp = await getAssistant("/api/assistant/deadline?state=florida");
    expect(resp.status).toBe(200);
    const body = (await resp.json()) as { records: { license_type: string }[] };
    const types = body.records.map((r) => r.license_type).sort();
    expect(types).toEqual(["firm", "individual"]);
  });

  it("400s on an unknown state slug", async () => {
    const resp = await getAssistant("/api/assistant/deadline?state=narnia");
    expect(resp.status).toBe(400);
  });

  it("400s with no state param at all", async () => {
    const resp = await getAssistant("/api/assistant/deadline");
    expect(resp.status).toBe(400);
  });

  it("404s a real state with no record for the requested license_type", async () => {
    const resp = await getAssistant("/api/assistant/deadline?state=texas&license_type=firm");
    expect(resp.status).toBe(404);
  });
});

describe("GET /assistant/cpe", () => {
  it("returns Texas's CPE requirement", async () => {
    const resp = await getAssistant("/api/assistant/cpe?state=texas");
    expect(resp.status).toBe(200);
    const rawBody = await resp.text();
    assertNoLeakedFields(rawBody);
    const body = JSON.parse(rawBody) as { state_slug: string; total_hours: number | null };
    expect(body.state_slug).toBe("texas");
  });

  it("400s on an unknown state", async () => {
    expect((await getAssistant("/api/assistant/cpe?state=narnia")).status).toBe(400);
  });
});

describe("GET /assistant/reinstatement", () => {
  it("returns Texas's reinstatement data", async () => {
    const resp = await getAssistant("/api/assistant/reinstatement?state=texas");
    expect(resp.status).toBe(200);
    const rawBody = await resp.text();
    assertNoLeakedFields(rawBody);
    const body = JSON.parse(rawBody) as { state_slug: string };
    expect(body.state_slug).toBe("texas");
  });

  it("400s on an unknown state", async () => {
    expect((await getAssistant("/api/assistant/reinstatement?state=narnia")).status).toBe(400);
  });
});

describe("GET /assistant/renewal-fee", () => {
  it("returns Texas's renewal fee", async () => {
    const resp = await getAssistant("/api/assistant/renewal-fee?state=texas");
    expect(resp.status).toBe(200);
    const rawBody = await resp.text();
    assertNoLeakedFields(rawBody);
    const body = JSON.parse(rawBody) as { state_slug: string };
    expect(body.state_slug).toBe("texas");
  });

  it("400s on an unknown state", async () => {
    expect((await getAssistant("/api/assistant/renewal-fee?state=narnia")).status).toBe(400);
  });
});

describe("GET /assistant/mobility", () => {
  it("returns a determination with the no-attestation note, reusing evaluateMobility()", async () => {
    const resp = await getAssistant("/api/assistant/mobility?home=texas&target=california&service_type=tax");
    expect(resp.status).toBe(200);
    const rawBody = await resp.text();
    assertNoLeakedFields(rawBody);
    const body = JSON.parse(rawBody) as {
      home_state: string;
      target_state: string;
      result: { individual: { verdict: string }; firm: { verdict: string }; overall: string };
      attestation_note: string;
    };
    expect(body.home_state).toBe("Texas");
    expect(body.target_state).toBe("California");
    expect(body.result.overall).toBeTruthy();
    expect(body.attestation_note.length).toBeGreaterThan(0);
  });

  it("400s on an invalid service_type", async () => {
    const resp = await getAssistant("/api/assistant/mobility?home=texas&target=california&service_type=not-a-real-type");
    expect(resp.status).toBe(400);
  });

  it("400s on an unknown home/target state", async () => {
    expect((await getAssistant("/api/assistant/mobility?home=narnia&target=california&service_type=tax")).status).toBe(400);
    expect((await getAssistant("/api/assistant/mobility?home=texas&target=narnia&service_type=tax")).status).toBe(400);
  });

  it("AuditLab LEAK-4 (2026-08-27): never leaks mobility_rules.json's internal data_gap_note, even for a state whose real record's note names internal fields by name", async () => {
    // Alaska's real record is one AuditLab found referencing flux_note/
    // firm_registration_attest by name in its own internal data_gap_note --
    // MobilityFinding no longer has a dataGapNote field at all (removed at
    // the source in mobility.ts, not just filtered here), so this is a
    // guaranteed-to-fail-loudly check if that's ever reintroduced.
    const resp = await getAssistant("/api/assistant/mobility?home=texas&target=alaska&service_type=tax");
    expect(resp.status).toBe(200);
    const rawBody = await resp.text();
    expect(rawBody).not.toContain("dataGapNote");
    expect(rawBody).not.toContain("flux_note");
    expect(rawBody).not.toContain("firm_registration_attest");
  });
});

describe("GET /assistant/rule-changes", () => {
  it("200s with an array (possibly empty) for a real state, never a 404", async () => {
    const resp = await getAssistant("/api/assistant/rule-changes?state=texas");
    expect(resp.status).toBe(200);
    const rawBody = await resp.text();
    assertNoLeakedFields(rawBody);
    const body = JSON.parse(rawBody) as { events: unknown[] };
    expect(Array.isArray(body.events)).toBe(true);
  });

  it("returns Guam's real source-conflict event with safe fields only", async () => {
    const resp = await getAssistant("/api/assistant/rule-changes?state=guam");
    expect(resp.status).toBe(200);
    const body = (await resp.json()) as { events: { summary_public: string; kind: string }[] };
    expect(body.events.length).toBeGreaterThan(0);
    expect(body.events[0]?.kind).toBe("source_conflict");
    expect(body.events[0]?.summary_public.length).toBeGreaterThan(0);
  });

  it("400s on an unknown state", async () => {
    expect((await getAssistant("/api/assistant/rule-changes?state=narnia")).status).toBe(400);
  });
});

describe("rate limiting -- shared 'assistant_api' bucket across all six routes", () => {
  it("429s once the shared bucket is exhausted, for ANY of the six routes", async () => {
    const ip = `203.0.113.${Math.floor(Math.random() * 200) + 1}`;
    // Pre-seed the bucket to its ceiling directly (RATE_LIMIT_ASSISTANT_API.max
    // = 600 -- hammering that many real HTTP round trips per test run isn't
    // worth it; checkRateLimit() itself is already proven correct by every
    // OTHER rate-limit test in this suite, this test is about confirming
    // THESE six routes actually call it with a shared bucket, not
    // re-proving the primitive).
    const now = Math.floor(Date.now() / 1000);
    const inserts = Array.from({ length: 600 }, (_, i) =>
      env.DB.prepare("INSERT INTO rate_limit_hits (ip, bucket, ts) VALUES (?1, ?2, ?3)").bind(ip, "assistant_api", now - i)
    );
    await env.DB.batch(inserts);

    const resp = await getAssistant("/api/assistant/cpe?state=texas", ip);
    expect(resp.status).toBe(429);

    // The SAME bucket blocks a different route too -- proves it's shared,
    // not accidentally six independent per-route buckets that just happen
    // to share a name.
    const resp2 = await getAssistant("/api/assistant/deadline?state=texas", ip);
    expect(resp2.status).toBe(429);
  });

  it("a fresh IP is unaffected by another IP's exhausted bucket", async () => {
    const resp = await getAssistant("/api/assistant/cpe?state=texas", "203.0.113.99");
    expect(resp.status).toBe(200);
  });
});
