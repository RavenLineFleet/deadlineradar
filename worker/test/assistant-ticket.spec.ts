/**
 * POST /assistant/ticket (2026-09-01, Devin: "walk the customer through a
 * ticket... send it to Support@ and it should go to Raven@."). Chat-native
 * escalation from the assistant widget to a real human -- no new
 * ticket-tracking table, the email itself IS the ticket (Devin's own
 * direction). Same in-process workerFetch()/fetch-mocking pattern as
 * assistant-chat.spec.ts (this route's own outbound call is to SendGrid,
 * not the droplet, but the same "real external service, mock it" reasoning
 * applies -- see that file's own docstring for why SELF.fetch can't be
 * used here).
 */
import { env } from "cloudflare:test";
import { describe, expect, it, vi } from "vitest";
import * as store from "../src/store";

const BASE = "https://deadline-radar.com";

function testExecutionContext(): ExecutionContext {
  return {
    waitUntil() {},
    passThroughOnException() {},
    props: {},
  } as unknown as ExecutionContext;
}

async function workerFetch(request: Request, envOverrides: Record<string, unknown> = {}): Promise<Response> {
  const worker = (await import("../src/index")).default;
  return worker.fetch(request, { ...env, ...envOverrides } as never, testExecutionContext());
}

function sendgridOk(): Response {
  return new Response("{}", { status: 202 });
}

// Unique per call by default -- RATE_LIMIT_ASSISTANT_TICKET is real D1
// state shared across every test in this file's own worker instance
// (5/hour/IP), so a fixed default IP would make the 6th+ test in the file
// collide with an earlier one's own budget, not the abuse case it exists
// to test. Tests that specifically WANT to share a bucket (none here) can
// still pass an explicit `ip`.
let ipCounter = 0;
function freshIp(): string {
  ipCounter += 1;
  return `203.0.113.${70 + ipCounter}`;
}

async function postTicket(
  body: unknown,
  opts: { ip?: string; origin?: string; cookie?: string; envOverrides?: Record<string, unknown> } = {}
): Promise<Response> {
  const headers: Record<string, string> = {
    "content-type": "application/json",
    "cf-connecting-ip": opts.ip ?? freshIp(),
    Origin: opts.origin ?? "https://deadline-radar.com",
  };
  if (opts.cookie) headers["Cookie"] = opts.cookie;
  return workerFetch(
    new Request(`${BASE}/api/assistant/ticket`, {
      method: "POST",
      headers,
      body: typeof body === "string" ? body : JSON.stringify(body),
    }),
    { SENDGRID_API_KEY: "test-key-not-real", ...opts.envOverrides }
  );
}

describe("POST /assistant/ticket -- validation", () => {
  it("rejects a cross-origin request (CSRF)", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(sendgridOk());
    try {
      const resp = await postTicket({ description: "Help", email: "visitor@example.com" }, { origin: "https://attacker.example" });
      expect(resp.status).toBe(400);
      expect(fetchSpy).not.toHaveBeenCalled();
    } finally {
      fetchSpy.mockRestore();
    }
  });

  it("rejects an empty description", async () => {
    const resp = await postTicket({ description: "  ", email: "visitor@example.com" });
    expect(resp.status).toBe(400);
  });

  it("rejects a description over the length cap", async () => {
    const resp = await postTicket({ description: "x".repeat(2001), email: "visitor@example.com" });
    expect(resp.status).toBe(400);
  });

  it("requires an email when not signed in -- 400 with the machine-readable code the widget branches on", async () => {
    const resp = await postTicket({ description: "Help with my renewal" });
    expect(resp.status).toBe(400);
    const body = (await resp.json()) as { error: string; code?: string };
    expect(body.code).toBe("email_required");
  });

  it("rejects a malformed email with its own code (so the widget keeps the email step open, not the description step)", async () => {
    const resp = await postTicket({ description: "Help", email: "not-an-email" });
    expect(resp.status).toBe(400);
    const body = (await resp.json()) as { error: string; code?: string };
    expect(body.code).toBe("email_invalid");
  });

  it("the signed-out visitor's email-less first attempt does NOT consume their rate-limit budget", async () => {
    // The widget always sends description-first (it can't see the HttpOnly
    // session cookies, so it can't know whether to ask for an email until
    // this route says so). Five of those probes from one IP, then a real
    // send from the SAME IP, must still go through -- if the probe counted,
    // the 6th call here would be a 429 instead of a real send.
    const ip = "203.0.113.250";
    for (let i = 0; i < 5; i++) {
      const probe = await postTicket({ description: "Help" }, { ip });
      expect(probe.status).toBe(400);
    }
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(sendgridOk());
    try {
      const resp = await postTicket({ description: "Help", email: "visitor@example.com" }, { ip });
      expect(resp.status).toBe(200);
      expect(fetchSpy).toHaveBeenCalledTimes(1);
    } finally {
      fetchSpy.mockRestore();
    }
  });

  it("429s once this IP has actually filed 5 tickets in the hour (the 'assistant_ticket' bucket, separate from 'assistant_chat')", async () => {
    const ip = "203.0.113.251";
    const now = Math.floor(Date.now() / 1000);
    const inserts = Array.from({ length: 5 }, (_, i) =>
      env.DB.prepare("INSERT INTO rate_limit_hits (ip, bucket, ts) VALUES (?1, ?2, ?3)").bind(ip, "assistant_ticket", now - i)
    );
    await env.DB.batch(inserts);
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(sendgridOk());
    try {
      const resp = await postTicket({ description: "Help", email: "visitor@example.com" }, { ip });
      expect(resp.status).toBe(429);
      expect(fetchSpy).not.toHaveBeenCalled();
    } finally {
      fetchSpy.mockRestore();
    }
  });

  it("an exhausted 'assistant_chat' bucket does NOT block a ticket -- the escalation path must still work when the chat itself is what's rate-limited", async () => {
    const ip = "203.0.113.252";
    const now = Math.floor(Date.now() / 1000);
    const inserts = Array.from({ length: 100 }, (_, i) =>
      env.DB.prepare("INSERT INTO rate_limit_hits (ip, bucket, ts) VALUES (?1, ?2, ?3)").bind(ip, "assistant_chat", now - i)
    );
    await env.DB.batch(inserts);
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(sendgridOk());
    try {
      const resp = await postTicket({ description: "Help", email: "visitor@example.com" }, { ip });
      expect(resp.status).toBe(200);
    } finally {
      fetchSpy.mockRestore();
    }
  });

  it("503s (not a false success) when SENDGRID_API_KEY is unset", async () => {
    const resp = await postTicket(
      { description: "Help", email: "visitor@example.com" },
      { envOverrides: { SENDGRID_API_KEY: undefined } }
    );
    expect(resp.status).toBe(503);
  });

  it("503s when the shared daily action-send cap is exhausted", async () => {
    const resp = await postTicket(
      { description: "Help", email: "visitor@example.com" },
      { envOverrides: { ACTION_DAILY_SEND_CAP: "0" } }
    );
    expect(resp.status).toBe(503);
  });
});

describe("POST /assistant/ticket -- success path, anonymous", () => {
  it("sends to support@deadline-radar.com with the visitor's email as reply-to", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(sendgridOk());
    try {
      const resp = await postTicket({ description: "I can't find my Ohio CPE hours.", email: "visitor@example.com" });
      expect(resp.status).toBe(200);
      const body = (await resp.json()) as { sent: boolean };
      expect(body.sent).toBe(true);

      expect(fetchSpy).toHaveBeenCalledTimes(1);
      const [url, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
      expect(String(url)).toContain("sendgrid.com");
      const payload = JSON.parse(init.body as string) as {
        personalizations: { to: { email: string }[] }[];
        reply_to?: { email: string };
        subject: string;
      };
      expect(payload.personalizations[0]?.to[0]?.email).toBe("support@deadline-radar.com");
      expect(payload.reply_to?.email).toBe("visitor@example.com");
      expect(payload.subject).toContain("visitor@example.com");
    } finally {
      fetchSpy.mockRestore();
    }
  });

  it("includes the description and session_id in the email body", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(sendgridOk());
    try {
      await postTicket({
        description: "I can't find my Ohio CPE hours.",
        email: "visitor@example.com",
        session_id: "sess-abc-123",
      });
      const [, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
      const payload = JSON.parse(init.body as string) as { content: { type: string; value: string }[] };
      const textBody = payload.content.find((c) => c.type === "text/plain")?.value ?? "";
      expect(textBody).toContain("I can't find my Ohio CPE hours.");
      expect(textBody).toContain("sess-abc-123");
    } finally {
      fetchSpy.mockRestore();
    }
  });

  it("rejects a client-forged session_id gibberish gracefully (treated as absent, not an error)", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(sendgridOk());
    try {
      const resp = await postTicket({ description: "Help", email: "visitor@example.com", session_id: 12345 });
      expect(resp.status).toBe(200);
    } finally {
      fetchSpy.mockRestore();
    }
  });
});

describe("POST /assistant/ticket -- signed-in visitors skip the email prompt", () => {
  async function subscriberCookie(email: string): Promise<string> {
    const { rawSessionToken } = await store.createSubscriberSession(env.DB, store.normalizeEmail(email));
    return `dr_sub_session=${rawSessionToken}`;
  }

  it("resolves the email from a subscriber session, ignoring any body.email", async () => {
    const email = `ticketsub-${Date.now()}@example.com`;
    await store.addPending(env.DB, {
      email,
      stateSlug: "ohio",
      deadlineFields: {},
      deadlineSource: store.DEADLINE_SOURCE_USER,
      userDeadline: "2027-01-01",
      firstName: null,
      firmId: null,
      skipConfirmation: true,
    });
    const cookie = await subscriberCookie(email);
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(sendgridOk());
    try {
      const resp = await postTicket({ description: "Help", email: "spoofed@attacker.example" }, { cookie });
      expect(resp.status).toBe(200);
      const [, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
      const payload = JSON.parse(init.body as string) as { reply_to?: { email: string } };
      expect(payload.reply_to?.email).toBe(store.normalizeEmail(email));
    } finally {
      fetchSpy.mockRestore();
    }
  });

  it("does NOT require a body.email when signed in", async () => {
    const email = `ticketsubnoemail-${Date.now()}@example.com`;
    await store.addPending(env.DB, {
      email,
      stateSlug: "ohio",
      deadlineFields: {},
      deadlineSource: store.DEADLINE_SOURCE_USER,
      userDeadline: "2027-01-01",
      firstName: null,
      firmId: null,
      skipConfirmation: true,
    });
    const cookie = await subscriberCookie(email);
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(sendgridOk());
    try {
      const resp = await postTicket({ description: "Help" }, { cookie });
      expect(resp.status).toBe(200);
    } finally {
      fetchSpy.mockRestore();
    }
  });

  it("resolves the email from a firm-dashboard session too", async () => {
    const { id: firmId, memberId } = await store.createFirm(env.DB, {
      name: "Ticket Firm Test LLC",
      adminEmail: `ticketfirmowner-${Date.now()}@example.com`,
    });
    const member = await store.getFirmMemberById(env.DB, firmId, memberId);
    const { rawSessionToken } = await store.createSession(env.DB, firmId, memberId);
    const cookie = `dr_firm_session=${rawSessionToken}`;
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(sendgridOk());
    try {
      const resp = await postTicket({ description: "Help from firm dashboard" }, { cookie });
      expect(resp.status).toBe(200);
      const [, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
      const payload = JSON.parse(init.body as string) as { reply_to?: { email: string } };
      expect(payload.reply_to?.email).toBe(member!.email);
    } finally {
      fetchSpy.mockRestore();
    }
  });

  it("the shared demo firm's session resolves NOTHING -- a demo visitor is asked for a real email, and it is the reply-to", async () => {
    const { id: firmId, memberId } = await store.createFirm(env.DB, {
      name: "Ticket Demo Firm LLC",
      adminEmail: `ticketdemo-${Date.now()}@example.com`,
    });
    await env.DB.prepare("UPDATE firms SET demo_locked = 1 WHERE id = ?1").bind(firmId).run();
    const { rawSessionToken } = await store.createSession(env.DB, firmId, memberId);
    const cookie = `dr_firm_session=${rawSessionToken}`;
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(sendgridOk());
    try {
      const noEmail = await postTicket({ description: "Help from the demo" }, { cookie });
      expect(noEmail.status).toBe(400);
      expect(((await noEmail.json()) as { code?: string }).code).toBe("email_required");

      const resp = await postTicket({ description: "Help from the demo", email: "realperson@example.com" }, { cookie });
      expect(resp.status).toBe(200);
      const [, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
      const payload = JSON.parse(init.body as string) as { reply_to?: { email: string } };
      expect(payload.reply_to?.email).toBe("realperson@example.com");
    } finally {
      fetchSpy.mockRestore();
      await env.DB.prepare("UPDATE firms SET demo_locked = 0 WHERE id = ?1").bind(firmId).run();
    }
  });
});
