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

describe("POST /assistant/chat -- ASSIST-1 retry (droplet's own canned-apology text at HTTP 200)", () => {
  it("a genuine first-attempt success does NOT retry -- exactly one fetch call", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(droplet("Texas renews annually on your birth month."));
    try {
      const resp = await postChat({ message: "When does Texas renew?" });
      expect(resp.status).toBe(200);
      expect(fetchSpy).toHaveBeenCalledTimes(1);
    } finally {
      fetchSpy.mockRestore();
    }
  });

  it("apology text on attempt 1, real answer on attempt 2 -- the real answer ships", async () => {
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(droplet("Something went wrong answering that just now -- not a problem with your question."))
      .mockResolvedValueOnce(droplet("Florida requires 80 CPE hours per 2-year period."));
    try {
      const resp = await postChat({ message: "Florida CPE hours?" });
      expect(resp.status).toBe(200);
      const body = (await resp.json()) as { reply: string };
      expect(body.reply).toBe("Florida requires 80 CPE hours per 2-year period.");
      expect(fetchSpy).toHaveBeenCalledTimes(2);
    } finally {
      fetchSpy.mockRestore();
    }
  });

  it("apology text on BOTH attempts -- surfaces the real (apology) reply, never fabricates one", async () => {
    // mockImplementation (not mockResolvedValue) so each call gets its OWN
    // fresh Response object -- a real fetch() never returns the same
    // Response instance twice, and a shared instance's body can only be
    // read once, which would make attempt 2's own resp.json() throw
    // (masking this test's actual intent behind a false 502).
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockImplementation(async () => droplet("Something went wrong answering that just now."));
    try {
      const resp = await postChat({ message: "hello" });
      expect(resp.status).toBe(200);
      const body = (await resp.json()) as { reply: string };
      expect(body.reply).toBe("Something went wrong answering that just now.");
      expect(fetchSpy).toHaveBeenCalledTimes(2);
    } finally {
      fetchSpy.mockRestore();
    }
  });

  it("a non-2xx on attempt 1, success on attempt 2 -- the retry's real answer ships", async () => {
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(new Response("error", { status: 500 }))
      .mockResolvedValueOnce(droplet("California renews every 2 years."));
    try {
      const resp = await postChat({ message: "California renewal cycle?" });
      expect(resp.status).toBe(200);
      const body = (await resp.json()) as { reply: string };
      expect(body.reply).toBe("California renews every 2 years.");
      expect(fetchSpy).toHaveBeenCalledTimes(2);
    } finally {
      fetchSpy.mockRestore();
    }
  });

  it("non-2xx on both attempts -- 502, exactly two fetch calls, no third attempt", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("error", { status: 500 }));
    try {
      const resp = await postChat({ message: "hello" });
      expect(resp.status).toBe(502);
      expect(fetchSpy).toHaveBeenCalledTimes(2);
    } finally {
      fetchSpy.mockRestore();
    }
  });

  it("network failure on both attempts -- 504, exactly two fetch calls", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("network down"));
    try {
      const resp = await postChat({ message: "hello" });
      expect(resp.status).toBe(504);
      expect(fetchSpy).toHaveBeenCalledTimes(2);
    } finally {
      fetchSpy.mockRestore();
    }
  });

  it("the failure-signature match is case-insensitive", async () => {
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(droplet("SOMETHING WENT WRONG ANSWERING THAT JUST NOW."))
      .mockResolvedValueOnce(droplet("A real answer."));
    try {
      const resp = await postChat({ message: "hello" });
      const body = (await resp.json()) as { reply: string };
      expect(body.reply).toBe("A real answer.");
      expect(fetchSpy).toHaveBeenCalledTimes(2);
    } finally {
      fetchSpy.mockRestore();
    }
  });

  it("a real answer that happens to discuss something going wrong with a filing does NOT trigger a retry", async () => {
    // Guards against the signature being so broad it false-positives on a
    // genuine answer -- the real droplet text is specifically about ITS OWN
    // failure to answer, not about renewal/filing problems in general.
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      droplet("If something goes wrong with your filing, contact the board directly.")
    );
    try {
      const resp = await postChat({ message: "What if my filing has an error?" });
      expect(resp.status).toBe(200);
      expect(fetchSpy).toHaveBeenCalledTimes(1);
    } finally {
      fetchSpy.mockRestore();
    }
  });
});

describe("POST /assistant/chat -- ASSIST-1 root cause: the droplet's own 429 rate limit must never be retried", () => {
  it("a 429 with a {reply} JSON body surfaces that text at 429, with exactly ONE fetch call (no retry)", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
      new Response(JSON.stringify({ reply: "Rate limited. It resets in about 50 minutes." }), { status: 429 })
    );
    try {
      const resp = await postChat({ message: "hello" });
      expect(resp.status).toBe(429);
      const body = (await resp.json()) as { error: string };
      expect(body.error).toBe("Rate limited. It resets in about 50 minutes.");
      expect(fetchSpy).toHaveBeenCalledTimes(1);
    } finally {
      fetchSpy.mockRestore();
    }
  });

  it("a 429 with a plain-text (non-JSON) body surfaces that text at 429, one fetch call", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
      new Response("Too many requests. It resets in about 50 minutes. Email support@deadline-radar.com if you need an answer sooner.", { status: 429 })
    );
    try {
      const resp = await postChat({ message: "hello" });
      expect(resp.status).toBe(429);
      const body = (await resp.json()) as { error: string };
      expect(body.error).toContain("resets in about 50 minutes");
      expect(fetchSpy).toHaveBeenCalledTimes(1);
    } finally {
      fetchSpy.mockRestore();
    }
  });

  it("a 429 with an empty body falls back to a generic rate-limited message, still no retry", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response("", { status: 429 }));
    try {
      const resp = await postChat({ message: "hello" });
      expect(resp.status).toBe(429);
      const body = (await resp.json()) as { error: string };
      expect(body.error.length).toBeGreaterThan(0);
      expect(fetchSpy).toHaveBeenCalledTimes(1);
    } finally {
      fetchSpy.mockRestore();
    }
  });

  it("attempt 1 is a genuine non-429 failure, attempt 2 is a 429 -- surfaces the 429 honestly (no third attempt)", async () => {
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockImplementationOnce(async () => new Response("error", { status: 500 }))
      .mockImplementationOnce(async () => new Response(JSON.stringify({ reply: "Rate limited now too." }), { status: 429 }));
    try {
      const resp = await postChat({ message: "hello" });
      expect(resp.status).toBe(429);
      const body = (await resp.json()) as { error: string };
      expect(body.error).toBe("Rate limited now too.");
      expect(fetchSpy).toHaveBeenCalledTimes(2);
    } finally {
      fetchSpy.mockRestore();
    }
  });
});

describe("POST /assistant/chat -- session_id forwarding (conversation continuity, orchestrator 2026-08-28)", () => {
  it("a valid session_id is forwarded to the droplet as-is", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(droplet("ok"));
    try {
      await postChat({ message: "hello", session_id: "abc-123-def" });
      const [, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
      expect(JSON.parse(init.body as string)).toEqual({ message: "hello", session_id: "abc-123-def" });
    } finally {
      fetchSpy.mockRestore();
    }
  });

  it("no session_id -- the droplet call omits the field entirely (same shape as before this feature)", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(droplet("ok"));
    try {
      await postChat({ message: "hello" });
      const [, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
      expect(JSON.parse(init.body as string)).toEqual({ message: "hello" });
    } finally {
      fetchSpy.mockRestore();
    }
  });

  it("a non-string session_id is dropped, not forwarded, and does not 400 the request", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(droplet("ok"));
    try {
      const resp = await postChat({ message: "hello", session_id: 12345 });
      expect(resp.status).toBe(200);
      const [, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
      expect(JSON.parse(init.body as string)).toEqual({ message: "hello" });
    } finally {
      fetchSpy.mockRestore();
    }
  });

  it("an oversized session_id (>200 chars) is dropped, not forwarded", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(droplet("ok"));
    try {
      const resp = await postChat({ message: "hello", session_id: "x".repeat(201) });
      expect(resp.status).toBe(200);
      const [, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
      expect(JSON.parse(init.body as string)).toEqual({ message: "hello" });
    } finally {
      fetchSpy.mockRestore();
    }
  });

  it("session_id is forwarded on the retry attempt too", async () => {
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockImplementationOnce(async () => new Response("error", { status: 500 }))
      .mockImplementationOnce(async () => droplet("a real answer"));
    try {
      await postChat({ message: "hello", session_id: "continuity-check" });
      expect(fetchSpy).toHaveBeenCalledTimes(2);
      for (const call of fetchSpy.mock.calls) {
        const init = call[1] as RequestInit;
        expect(JSON.parse(init.body as string)).toEqual({ message: "hello", session_id: "continuity-check" });
      }
    } finally {
      fetchSpy.mockRestore();
    }
  });
});

describe("POST /assistant/chat -- real visitor IP forwarded to the droplet (ShopLab, 2026-08-28)", () => {
  it("the inbound cf-connecting-ip is forwarded as CF-Connecting-IP on the outbound droplet call", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(droplet("ok"));
    try {
      await postChat({ message: "hello" }, { ip: "198.51.100.42" });
      const [, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
      var headers = new Headers(init.headers);
      expect(headers.get("CF-Connecting-IP")).toBe("198.51.100.42");
    } finally {
      fetchSpy.mockRestore();
    }
  });

  it("a request with no cf-connecting-ip header forwards the documented 0.0.0.0 fallback, not an omitted header", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(droplet("ok"));
    try {
      const resp = await workerFetch(
        new Request(`${BASE}/api/assistant/chat`, {
          method: "POST",
          headers: { "content-type": "application/json", Origin: "https://deadline-radar.com" },
          body: JSON.stringify({ message: "hello" }),
        })
      );
      expect(resp.status).toBe(200);
      const [, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
      var headers = new Headers(init.headers);
      expect(headers.get("CF-Connecting-IP")).toBe("0.0.0.0");
    } finally {
      fetchSpy.mockRestore();
    }
  });

  it("the forwarded IP is the SAME on a retry attempt, not dropped or altered", async () => {
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockImplementationOnce(async () => new Response("error", { status: 500 }))
      .mockImplementationOnce(async () => droplet("a real answer"));
    try {
      await postChat({ message: "hello" }, { ip: "198.51.100.99" });
      expect(fetchSpy).toHaveBeenCalledTimes(2);
      for (const call of fetchSpy.mock.calls) {
        var headers = new Headers((call[1] as RequestInit).headers);
        expect(headers.get("CF-Connecting-IP")).toBe("198.51.100.99");
      }
    } finally {
      fetchSpy.mockRestore();
    }
  });
});
