/**
 * POST /assistant/chat (2026-08-28) -- the same-origin proxy backing the
 * site-wide chat widget. Orchestrator's droplet is a real external service
 * (no CORS headers of its own, confirmed live), so every test here mocks
 * globalThis.fetch rather than hitting it -- this file proves the PROXY's
 * own behavior (origin check, rate limit, body validation, error mapping),
 * not the droplet's own answers, which is orchestrator's own testing
 * surface.
 *
 * Uses the in-process workerFetch() helper (worker.fetch() called directly,
 * same realm as this test file), not SELF.fetch -- SELF.fetch dispatches to
 * a genuinely separate Worker instance under vitest-pool-workers, whose own
 * global fetch is NOT the one vi.spyOn(globalThis, "fetch") here patches.
 * Confirmed the hard way: the first draft of this file used SELF.fetch and
 * every mocked-success test failed with a real 502 (the worker's outbound
 * fetch to the mocked-away droplet host actually left the sandbox and got a
 * network-blocked response, never touching the mock) -- switching to
 * workerFetch(), the same pattern action-send-budget.spec.ts already uses
 * for its own outbound-fetch (SendGrid) mocking, fixed it.
 */
import { env } from "cloudflare:test";
import { describe, expect, it, vi } from "vitest";

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

function droplet(reply: string): Response {
  return new Response(JSON.stringify({ reply }), { status: 200 });
}

async function postChat(
  body: unknown,
  opts: { ip?: string; origin?: string } = {}
): Promise<Response> {
  return workerFetch(
    new Request(`${BASE}/api/assistant/chat`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "cf-connecting-ip": opts.ip ?? "203.0.113.60",
        Origin: opts.origin ?? "https://deadline-radar.com",
      },
      body: typeof body === "string" ? body : JSON.stringify(body),
    })
  );
}

describe("POST /assistant/chat -- success path", () => {
  it("forwards the message to the droplet and returns its reply", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(droplet("California renews every 2 years."));
    try {
      const resp = await postChat({ message: "When does California renew?" });
      expect(resp.status).toBe(200);
      const body = (await resp.json()) as { reply: string };
      expect(body.reply).toBe("California renews every 2 years.");

      expect(fetchSpy).toHaveBeenCalledTimes(1);
      const [url, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
      expect(url).toBe("https://deadlineradar-assistant.143-198-52-110.nip.io/chat");
      expect(init.method).toBe("POST");
      expect(JSON.parse(init.body as string)).toEqual({ message: "When does California renew?" });
    } finally {
      fetchSpy.mockRestore();
    }
  });

  it("trims whitespace off the message before forwarding it", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(droplet("ok"));
    try {
      await postChat({ message: "  hello  " });
      const [, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
      expect(JSON.parse(init.body as string)).toEqual({ message: "hello" });
    } finally {
      fetchSpy.mockRestore();
    }
  });
});

describe("POST /assistant/chat -- request validation, never reaches the droplet", () => {
  it("400s on an empty message, no fetch attempted", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    try {
      const resp = await postChat({ message: "" });
      expect(resp.status).toBe(400);
      expect(fetchSpy).not.toHaveBeenCalled();
    } finally {
      fetchSpy.mockRestore();
    }
  });

  it("400s on a whitespace-only message", async () => {
    const resp = await postChat({ message: "   " });
    expect(resp.status).toBe(400);
  });

  it("400s on a missing message field", async () => {
    const resp = await postChat({});
    expect(resp.status).toBe(400);
  });

  it("400s on a message over the 2000-char cap", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    try {
      const resp = await postChat({ message: "a".repeat(2001) });
      expect(resp.status).toBe(400);
      expect(fetchSpy).not.toHaveBeenCalled();
    } finally {
      fetchSpy.mockRestore();
    }
  });

  it("400s on malformed JSON", async () => {
    const resp = await postChat("{not json");
    expect(resp.status).toBe(400);
  });

  it("400s on a JSON array instead of an object", async () => {
    const resp = await postChat(["hello"]);
    expect(resp.status).toBe(400);
  });
});

describe("POST /assistant/chat -- droplet failure modes", () => {
  it("502s when the droplet returns a non-2xx status", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("error", { status: 500 }));
    try {
      const resp = await postChat({ message: "hello" });
      expect(resp.status).toBe(502);
    } finally {
      fetchSpy.mockRestore();
    }
  });

  it("502s when the droplet's body isn't valid JSON", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("not json", { status: 200 }));
    try {
      const resp = await postChat({ message: "hello" });
      expect(resp.status).toBe(502);
    } finally {
      fetchSpy.mockRestore();
    }
  });

  it("502s when the droplet's JSON has no string reply field", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ reply: 42 }), { status: 200 }));
    try {
      const resp = await postChat({ message: "hello" });
      expect(resp.status).toBe(502);
    } finally {
      fetchSpy.mockRestore();
    }
  });

  it("504s when the droplet fetch throws (network failure / timeout)", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("network down"));
    try {
      const resp = await postChat({ message: "hello" });
      expect(resp.status).toBe(504);
    } finally {
      fetchSpy.mockRestore();
    }
  });
});

describe("POST /assistant/chat -- cross-site origin rejected", () => {
  it("400s a forged cross-site request, no fetch attempted", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    try {
      const resp = await postChat({ message: "hello" }, { origin: "https://evil.example" });
      expect(resp.status).toBe(400);
      expect(fetchSpy).not.toHaveBeenCalled();
    } finally {
      fetchSpy.mockRestore();
    }
  });
});

describe("POST /assistant/chat -- rate limiting ('assistant_chat' bucket, 100/hour)", () => {
  it("429s once this IP's bucket is exhausted", async () => {
    const ip = `203.0.113.${Math.floor(Math.random() * 200) + 1}`;
    const now = Math.floor(Date.now() / 1000);
    const inserts = Array.from({ length: 100 }, (_, i) =>
      env.DB.prepare("INSERT INTO rate_limit_hits (ip, bucket, ts) VALUES (?1, ?2, ?3)").bind(ip, "assistant_chat", now - i)
    );
    await env.DB.batch(inserts);

    const fetchSpy = vi.spyOn(globalThis, "fetch");
    try {
      const resp = await postChat({ message: "hello" }, { ip });
      expect(resp.status).toBe(429);
      expect(fetchSpy).not.toHaveBeenCalled();
    } finally {
      fetchSpy.mockRestore();
    }
  });

  it("a fresh IP is unaffected by another IP's exhausted bucket", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(droplet("ok"));
    try {
      const resp = await postChat({ message: "hello" }, { ip: "203.0.113.201" });
      expect(resp.status).toBe(200);
    } finally {
      fetchSpy.mockRestore();
    }
  });

  it("exhausting the unrelated 'assistant_api' bucket does not block this route", async () => {
    // Proves this is genuinely a SEPARATE, tighter bucket from
    // RATE_LIMIT_ASSISTANT_API (600/hour, shared by the 6 lookup routes),
    // not accidentally reusing that name.
    const ip = `203.0.113.${Math.floor(Math.random() * 55) + 1}`;
    const now = Math.floor(Date.now() / 1000);
    const inserts = Array.from({ length: 600 }, (_, i) =>
      env.DB.prepare("INSERT INTO rate_limit_hits (ip, bucket, ts) VALUES (?1, ?2, ?3)").bind(ip, "assistant_api", now - i)
    );
    await env.DB.batch(inserts);

    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(droplet("ok"));
    try {
      const resp = await postChat({ message: "hello" }, { ip });
      expect(resp.status).toBe(200);
    } finally {
      fetchSpy.mockRestore();
    }
  });
});
