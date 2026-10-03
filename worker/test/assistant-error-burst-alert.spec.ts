/**
 * MON-9 (AuditLab, MEDIUM, 2026-10-02): the existing assistant-latency
 * alert (assistant-latency-alert.spec.ts) computes p95/max from
 * status='success' rows only -- a total outage where every leg fails FAST
 * (the droplet's own apology, or a thrown exception) actually LOWERS p95,
 * making that alert strictly less likely to fire during the exact failure
 * it exists to catch. Confirmed live: the 2026-10-02 chat outage (claude
 * CLI missing from the droplet service user's PATH) was logged entirely
 * as 'success' at ~1.5-3s. This file covers the complementary, count-based
 * signal: recentAssistantChatErrorBurstStats() and
 * runAssistantErrorBurstAlertPass().
 */
import { env } from "cloudflare:test";
import { describe, expect, it, vi } from "vitest";
import * as store from "../src/store";

const RESEND_URL = "https://api.resend.com/emails";

async function seedRows(count: number, status: store.AssistantChatLatencyStatus): Promise<void> {
  const now = Math.floor(Date.now() / 1000);
  for (let i = 0; i < count; i++) {
    await store.logAssistantChatLatency(env.DB, 1500, now, status);
  }
}

describe("recentAssistantChatErrorBurstStats -- count-based window, most-recent N", () => {
  it("returns zero/zero with nothing logged", async () => {
    await env.DB.prepare("DELETE FROM assistant_chat_latency_log").run();
    const stats = await store.recentAssistantChatErrorBurstStats(env.DB, 10);
    expect(stats).toEqual({ windowCount: 0, errorCount: 0 });
  });

  it("windowCount is capped at the limit even with far more rows available", async () => {
    await env.DB.prepare("DELETE FROM assistant_chat_latency_log").run();
    await seedRows(25, "success");
    const stats = await store.recentAssistantChatErrorBurstStats(env.DB, 10);
    expect(stats.windowCount).toBe(10);
  });

  it("windowCount is less than the limit when fewer rows exist -- not padded or treated as zero", async () => {
    await env.DB.prepare("DELETE FROM assistant_chat_latency_log").run();
    await seedRows(4, "success");
    const stats = await store.recentAssistantChatErrorBurstStats(env.DB, 10);
    expect(stats.windowCount).toBe(4);
    expect(stats.errorCount).toBe(0);
  });

  it("rate_limited does NOT count as an error -- it's an expected signal, not a backend failure", async () => {
    await env.DB.prepare("DELETE FROM assistant_chat_latency_log").run();
    await seedRows(10, "rate_limited");
    const stats = await store.recentAssistantChatErrorBurstStats(env.DB, 10);
    expect(stats).toEqual({ windowCount: 10, errorCount: 0 });
  });

  it("only looks at the MOST RECENT window -- an old burst outside the window doesn't count", async () => {
    await env.DB.prepare("DELETE FROM assistant_chat_latency_log").run();
    await seedRows(10, "error"); // old burst, will fall outside the 5-row window below
    await seedRows(5, "success"); // recent, clean
    const stats = await store.recentAssistantChatErrorBurstStats(env.DB, 5);
    expect(stats).toEqual({ windowCount: 5, errorCount: 0 });
  });
});

describe("runAssistantErrorBurstAlertPass -- the gated, thresholded send", () => {
  async function freshRun(overrides: Record<string, unknown>) {
    const { runAssistantErrorBurstAlertPass } = await import("../src/scheduler");
    return runAssistantErrorBurstAlertPass({ ...env, ...overrides } as never);
  }

  it("does nothing when SEND_APPROVED_PASSES doesn't include this pass -- fails closed by default", async () => {
    await env.DB.prepare("DELETE FROM assistant_chat_latency_log").run();
    await env.DB.prepare("DELETE FROM assistant_error_burst_alert_log").run();
    await seedRows(10, "error");
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    try {
      await freshRun({ SEND_APPROVED_PASSES: undefined, RESEND_API_KEY: "test-key" });
      expect(fetchSpy).not.toHaveBeenCalled();
    } finally {
      fetchSpy.mockRestore();
    }
  });

  it("fewer than the count threshold total samples -- no send even at 100% error (a 1-of-1 window means nothing)", async () => {
    await env.DB.prepare("DELETE FROM assistant_chat_latency_log").run();
    await env.DB.prepare("DELETE FROM assistant_error_burst_alert_log").run();
    await seedRows(2, "error");
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    try {
      await freshRun({ SEND_APPROVED_PASSES: "assistantErrorBurstAlert", RESEND_API_KEY: "test-key" });
      expect(fetchSpy).not.toHaveBeenCalled();
    } finally {
      fetchSpy.mockRestore();
    }
  });

  it("MON-9 regression: the exact outage shape (apology logged as error, fast elapsed time) fires THIS alert where the p95-only alert stays silent", async () => {
    await env.DB.prepare("DELETE FROM assistant_chat_latency_log").run();
    await env.DB.prepare("DELETE FROM assistant_latency_alert_log").run();
    await env.DB.prepare("DELETE FROM assistant_error_burst_alert_log").run();
    const now = Math.floor(Date.now() / 1000);
    // The live outage's exact shape: fast (1.5s), logged as 'error' (post-fix).
    for (let i = 0; i < 10; i++) {
      await store.logAssistantChatLatency(env.DB, 1500, now, "error");
    }

    // Sanity: the OLD alert's own stats function sees this as a non-event --
    // zero success samples means n=0, so runAssistantLatencyAlertPass would
    // return immediately without ever evaluating a threshold.
    const oldStats = await store.recentAssistantChatLatencyStats(env.DB, now, 86400);
    expect(oldStats.n).toBe(0);

    const captured: Array<{ subject: string; text: string }> = [];
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = typeof input === "string" ? input : (input as Request).url;
      if (url === RESEND_URL) {
        const body = JSON.parse(String(init?.body)) as { subject: string; text: string };
        captured.push({ subject: body.subject, text: body.text ?? "" });
        return new Response(null, { status: 202 });
      }
      throw new Error(`unexpected fetch in assistant-error-burst-alert test: ${url}`);
    });
    try {
      await freshRun({ SEND_APPROVED_PASSES: "assistantErrorBurstAlert", RESEND_API_KEY: "test-key" });
      expect(captured).toHaveLength(1);
      expect(captured[0]?.subject).toContain("error burst");
      expect(captured[0]?.subject).toContain("10/10");
      expect(captured[0]?.text).toContain("10 of the last 10");
    } finally {
      fetchSpy.mockRestore();
    }
  });

  it("low volume, below the count floor but at/above the 50% rate threshold -- still sends", async () => {
    await env.DB.prepare("DELETE FROM assistant_chat_latency_log").run();
    await env.DB.prepare("DELETE FROM assistant_error_burst_alert_log").run();
    // 2 errors of 3 total: errorCount(2) < countThreshold(3), but rate
    // (2/3 = 67%) >= 50% -- isolates the rate branch from the count branch.
    await seedRows(2, "error");
    await seedRows(1, "success");
    const captured: string[] = [];
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = typeof input === "string" ? input : (input as Request).url;
      if (url === RESEND_URL) {
        captured.push(JSON.parse(String(init?.body)).subject as string);
        return new Response(null, { status: 202 });
      }
      throw new Error(`unexpected fetch: ${url}`);
    });
    try {
      await freshRun({ SEND_APPROVED_PASSES: "assistantErrorBurstAlert", RESEND_API_KEY: "test-key" });
      expect(captured).toHaveLength(1);
    } finally {
      fetchSpy.mockRestore();
    }
  });

  it("high volume, 2 errors of 10 (20%, under both thresholds) -- no send", async () => {
    await env.DB.prepare("DELETE FROM assistant_chat_latency_log").run();
    await env.DB.prepare("DELETE FROM assistant_error_burst_alert_log").run();
    await seedRows(2, "error");
    await seedRows(8, "success");
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    try {
      await freshRun({ SEND_APPROVED_PASSES: "assistantErrorBurstAlert", RESEND_API_KEY: "test-key" });
      expect(fetchSpy).not.toHaveBeenCalled();
    } finally {
      fetchSpy.mockRestore();
    }
  });

  it("a breach sends at most once per UTC day -- a second call the same day is a no-op", async () => {
    await env.DB.prepare("DELETE FROM assistant_chat_latency_log").run();
    await env.DB.prepare("DELETE FROM assistant_error_burst_alert_log").run();
    await seedRows(10, "error");
    let sendCount = 0;
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = typeof input === "string" ? input : (input as Request).url;
      if (url === RESEND_URL) {
        sendCount++;
        return new Response(null, { status: 202 });
      }
      throw new Error(`unexpected fetch: ${url}`);
    });
    try {
      await freshRun({ SEND_APPROVED_PASSES: "assistantErrorBurstAlert", RESEND_API_KEY: "test-key" });
      await freshRun({ SEND_APPROVED_PASSES: "assistantErrorBurstAlert", RESEND_API_KEY: "test-key" });
      expect(sendCount).toBe(1);
    } finally {
      fetchSpy.mockRestore();
    }
  });
});
