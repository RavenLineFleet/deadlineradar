/**
 * Self-reported "How did you hear about us?" (Orchestrator directive,
 * 2026-10-28, Devin-approved; migration 0092). Verifies the allow-list gate
 * (an off-list how_heard is stored NULL, never as-is), that the free-text
 * follow-up is kept only for referral/other and is capped, that skipping the
 * question never blocks a signup, and that the operator notification shows
 * the answer.
 */
import { env, SELF } from "cloudflare:test";
import { describe, expect, it } from "vitest";
import { buildSignupNotificationEmail } from "../src/emails";
import { addPending } from "../src/store";

const BASE = "https://deadline-radar.com";

function form(fields: Record<string, string>): string {
  return new URLSearchParams(fields).toString();
}

async function subscribe(fields: Record<string, string>, ip: string): Promise<Response> {
  return SELF.fetch(`${BASE}/subscribe`, {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded", "cf-connecting-ip": ip },
    body: form({ state: "georgia", license_type_id: "ga-individual", hp_website: "", ...fields }),
  });
}

async function rowFor(email: string): Promise<{ how_heard: string | null; how_heard_detail: string | null } | null> {
  return env.DB.prepare("SELECT how_heard, how_heard_detail FROM subscribers WHERE email = ?1")
    .bind(email)
    .first<{ how_heard: string | null; how_heard_detail: string | null }>();
}

describe("POST /subscribe -- how_heard", () => {
  it("stores an allow-listed answer and drops the detail for a non-detail choice", async () => {
    const email = `hh-linkedin-${Date.now()}@example.com`;
    const resp = await subscribe({ email, how_heard: "linkedin", how_heard_detail: "should be ignored" }, "203.0.113.70");
    expect(resp.status).toBe(200);
    expect(await rowFor(email)).toEqual({ how_heard: "linkedin", how_heard_detail: null });
  });

  it("keeps a capped detail for referral", async () => {
    const email = `hh-referral-${Date.now()}@example.com`;
    const resp = await subscribe({ email, how_heard: "referral", how_heard_detail: "  " + "a".repeat(300) }, "203.0.113.71");
    expect(resp.status).toBe(200);
    const row = await rowFor(email);
    expect(row?.how_heard).toBe("referral");
    expect(row?.how_heard_detail).toBe("a".repeat(120));
  });

  it("keeps the detail for other", async () => {
    const email = `hh-other-${Date.now()}@example.com`;
    await subscribe({ email, how_heard: "other", how_heard_detail: "State society newsletter" }, "203.0.113.72");
    expect(await rowFor(email)).toEqual({ how_heard: "other", how_heard_detail: "State society newsletter" });
  });

  it("an off-allow-list value is stored NULL, detail too -- never trust the client past the allow-list", async () => {
    const email = `hh-bogus-${Date.now()}@example.com`;
    const resp = await subscribe({ email, how_heard: "<script>alert(1)</script>", how_heard_detail: "x" }, "203.0.113.73");
    expect(resp.status).toBe(200);
    expect(await rowFor(email)).toEqual({ how_heard: null, how_heard_detail: null });
  });

  it("skipping the question never blocks the signup", async () => {
    const email = `hh-skipped-${Date.now()}@example.com`;
    const resp = await subscribe({ email }, "203.0.113.74");
    expect(resp.status).toBe(200);
    expect(await rowFor(email)).toEqual({ how_heard: null, how_heard_detail: null });
  });

  it("a control character in the detail is rejected like every other field", async () => {
    const email = `hh-ctrl-${Date.now()}@example.com`;
    const resp = await subscribe({ email, how_heard: "other", how_heard_detail: "line1\r\nBcc: x@example.com" }, "203.0.113.75");
    expect(resp.status).toBe(400);
    expect(await rowFor(email)).toBeNull();
  });
});

describe("addPending -- how_heard_detail guard", () => {
  // AuditLab (0092 review, LOW): the store layer re-applies the referral/other
  // gate itself rather than trusting every caller to have nulled the detail.
  it("drops a detail passed alongside a non-detail choice", async () => {
    const email = `hh-store-search-${Date.now()}@example.com`;
    await addPending(env.DB, {
      email, stateSlug: "georgia", deadlineFields: {}, firstName: null,
      howHeard: "search", howHeardDetail: "should not be stored",
    });
    expect(await rowFor(email)).toEqual({ how_heard: "search", how_heard_detail: null });
  });

  it("keeps a detail passed alongside referral", async () => {
    const email = `hh-store-referral-${Date.now()}@example.com`;
    await addPending(env.DB, {
      email, stateSlug: "georgia", deadlineFields: {}, firstName: null,
      howHeard: "referral", howHeardDetail: "a colleague",
    });
    expect(await rowFor(email)).toEqual({ how_heard: "referral", how_heard_detail: "a colleague" });
  });
});

describe("buildSignupNotificationEmail -- how they heard", () => {
  it("shows the label and detail, escaped in HTML", () => {
    const built = buildSignupNotificationEmail("individual", {
      email: "a@example.com",
      stateName: "Georgia",
      howHeard: "Referred by someone",
      howHeardDetail: "<b>Pat</b>",
    });
    expect(built.textBody).toContain("How they heard: Referred by someone -- <b>Pat</b>");
    expect(built.htmlBody).toContain("How they heard: Referred by someone -- &lt;b&gt;Pat&lt;/b&gt;");
  });

  it("says (not answered) when skipped", () => {
    const built = buildSignupNotificationEmail("individual", { email: "a@example.com", stateName: "Georgia" });
    expect(built.textBody).toContain("How they heard: (not answered)");
  });
});
