/**
 * AuditLab (2026-08-31, Devin's "get to 100%" latency-monitoring
 * directive): confirmed the assistant chat endpoint sits in a ~15-18s
 * steady state for a normal question (recurring across independent
 * samples, not a one-off). This is the persistent-in-prod half AuditLab's
 * read-only charter can't build itself: log every real request's elapsed
 * time (index.ts's callers of store.logAssistantChatLatency), and a daily
 * cron pass (runAssistantLatencyAlertPass) alerts only on a genuine
 * departure from that known baseline (p95 > 25s or any sample > 30s),
 * never on routine variance.
 */
import { env } from "cloudflare:test";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as store from "../src/store";

const SENDGRID_URL = "https://api.sendgrid.com/v3/mail/send";

async function seedSamples(
  nowSeconds: number,
  elapsedMsValues: number[],
  status: store.AssistantChatLatencyStatus = "success"
): Promise<void> {
  for (const ms of elapsedMsValues) {
    await store.logAssistantChatLatency(env.DB, ms, nowSeconds, status);
  }
}

describe("recentAssistantChatLatencyStats -- p95/max computation and self-trimming", () => {
  it("returns null stats (not 0) when there are no samples in the window", async () => {
    const now = 5_000_000;
    const stats = await store.recentAssistantChatLatencyStats(env.DB, now, 86400);
    expect(stats).toEqual({ n: 0, p95Ms: null, maxMs: null, totalN: 0 });
  });

  it("computes p95 via nearest-rank on a known 20-sample set", async () => {
    const now = 5_100_000;
    // 20 samples, 1000ms through 20000ms in 1000ms steps. Nearest-rank p95
    // of n=20 is ceil(0.95*20)=19th smallest (1-indexed) = 19000ms.
    const values = Array.from({ length: 20 }, (_, i) => (i + 1) * 1000);
    await seedSamples(now, values);
    const stats = await store.recentAssistantChatLatencyStats(env.DB, now, 3600);
    expect(stats.n).toBe(20);
    expect(stats.p95Ms).toBe(19000);
    expect(stats.maxMs).toBe(20000);
  });

  it("a single sample is both its own p95 and max", async () => {
    const now = 5_200_000;
    await seedSamples(now, [7777]);
    const stats = await store.recentAssistantChatLatencyStats(env.DB, now, 3600);
    expect(stats).toEqual({ n: 1, p95Ms: 7777, maxMs: 7777, totalN: 1 });
  });

  it("excludes samples outside the lookback window", async () => {
    const now = 5_300_000;
    await store.logAssistantChatLatency(env.DB, 15000, now - 3601, "success"); // just outside a 1h window
    await store.logAssistantChatLatency(env.DB, 16000, now - 100, "success"); // inside
    const stats = await store.recentAssistantChatLatencyStats(env.DB, now, 3600);
    expect(stats.n).toBe(1);
    expect(stats.p95Ms).toBe(16000);
  });

  it("self-trims: a query for a window PRUNES rows older than that window, even for a later query with a wider window", async () => {
    const now = 5_400_000;
    await store.logAssistantChatLatency(env.DB, 99999, now - 7200, "success"); // 2h old
    await store.logAssistantChatLatency(env.DB, 15000, now - 100, "success");
    // First query with a 1h window deletes the 2h-old row (cutoff = now - 3600).
    const narrow = await store.recentAssistantChatLatencyStats(env.DB, now, 3600);
    expect(narrow.n).toBe(1);
    // A second query with a wider window (24h) would have included the
    // pruned row had it survived -- confirms it's actually gone, not just
    // excluded by the first query's own cutoff.
    const wide = await store.recentAssistantChatLatencyStats(env.DB, now, 86400);
    expect(wide.n).toBe(1);
    expect(wide.p95Ms).toBe(15000);
  });

  it("logAssistantChatLatency never throws, even against a nonsense db handle", async () => {
    const brokenDb = { prepare: () => { throw new Error("simulated D1 outage"); } } as unknown as D1Database;
    await expect(store.logAssistantChatLatency(brokenDb, 5000, 123, "success")).resolves.toBeUndefined();
  });

  it("MON-4 (AuditLab, 2026-08-31): 429 fast-rejects never dilute p95/max -- success-only stats, totalN counts everything", async () => {
    const now = 5_500_000;
    // AuditLab's own simulation shape: a rate-limit storm alongside a few
    // genuinely slow real answers. All-rows p95 would read near-instant
    // (masking the real 26s degradation); success-only p95 must not.
    await seedSamples(now, Array.from({ length: 950 }, () => 400), "rate_limited");
    await seedSamples(now, Array.from({ length: 50 }, () => 26000), "success");
    const stats = await store.recentAssistantChatLatencyStats(env.DB, now, 3600);
    expect(stats.n).toBe(50); // only the success rows feed p95/max
    expect(stats.p95Ms).toBe(26000);
    expect(stats.maxMs).toBe(26000);
    expect(stats.totalN).toBe(1000); // but the 429 volume is still visible
  });

  it("MON-4: an all-429 window (zero successes) reports null p95/max, not 0 -- never mistaken for 'fast and healthy'", async () => {
    const now = 5_600_000;
    await seedSamples(now, [400, 400, 400], "rate_limited");
    const stats = await store.recentAssistantChatLatencyStats(env.DB, now, 3600);
    expect(stats).toEqual({ n: 0, p95Ms: null, maxMs: null, totalN: 3 });
  });

  it("MON-4: 'error' status is also excluded from p95/max, same as 'rate_limited'", async () => {
    const now = 5_700_000;
    await seedSamples(now, [500, 600], "error");
    await seedSamples(now, [18000], "success");
    const stats = await store.recentAssistantChatLatencyStats(env.DB, now, 3600);
    expect(stats).toEqual({ n: 1, p95Ms: 18000, maxMs: 18000, totalN: 3 });
  });
});

describe("claimAssistantLatencyAlertForToday / unclaim -- day-keyed dedup", () => {
  it("first claim for a day succeeds, a second claim the same day fails", async () => {
    const day = "2099-01-15";
    expect(await store.claimAssistantLatencyAlertForToday(env.DB, day)).toBe(true);
    expect(await store.claimAssistantLatencyAlertForToday(env.DB, day)).toBe(false);
  });

  it("unclaim releases the day so a later attempt can claim it again", async () => {
    const day = "2099-01-16";
    expect(await store.claimAssistantLatencyAlertForToday(env.DB, day)).toBe(true);
    await store.unclaimAssistantLatencyAlertForToday(env.DB, day);
    expect(await store.claimAssistantLatencyAlertForToday(env.DB, day)).toBe(true);
  });

  it("different days are independent", async () => {
    expect(await store.claimAssistantLatencyAlertForToday(env.DB, "2099-02-01")).toBe(true);
    expect(await store.claimAssistantLatencyAlertForToday(env.DB, "2099-02-02")).toBe(true);
  });
});

describe("runAssistantLatencyAlertPass -- the gated, thresholded send", () => {
  // The pass itself queries "last 24h from real Date.now()" with no way to
  // inject a synthetic clock (unlike the store-level tests above, which
  // isolate via far-future synthetic timestamps) -- every test in this
  // block shares the same 24h real-time window, so a prior test's seeded
  // rows would otherwise silently inflate a later test's p95/max. Full
  // reset before each test, not just the alert-dedup log, since a stray
  // sample changes N/p95/max regardless of whether it's a claim leak.
  beforeEach(async () => {
    await env.DB.prepare("DELETE FROM assistant_chat_latency_log").run();
    await env.DB.prepare("DELETE FROM assistant_latency_alert_log").run();
  });

  async function freshRun(overrides: Record<string, unknown>) {
    const { runAssistantLatencyAlertPass } = await import("../src/scheduler");
    return runAssistantLatencyAlertPass({ ...env, ...overrides } as never);
  }

  it("does nothing (no fetch call at all) when SEND_APPROVED_PASSES doesn't include this pass -- fails closed by default", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    try {
      await store.logAssistantChatLatency(env.DB, 40000, Math.floor(Date.now() / 1000), "success");
      await freshRun({ SEND_APPROVED_PASSES: undefined, SENDGRID_API_KEY: "test-key" });
      expect(fetchSpy).not.toHaveBeenCalled();
    } finally {
      fetchSpy.mockRestore();
    }
  });

  it("approved, but under both thresholds -- no send (the known ~15-18s baseline must never trip this)", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    try {
      const now = Math.floor(Date.now() / 1000);
      // Simulate the real documented baseline: values in the 15-18s band.
      await seedSamples(now, [15000, 16000, 17000, 18000, 15500]);
      await freshRun({ SEND_APPROVED_PASSES: "assistantLatencyAlert", SENDGRID_API_KEY: "test-key" });
      expect(fetchSpy).not.toHaveBeenCalled();
    } finally {
      fetchSpy.mockRestore();
    }
  });

  it("approved, max threshold breached (one sample over 30s) -- sends the alert with correct stats", async () => {
    const captured: Array<{ to: unknown; subject: string; text: string }> = [];
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = typeof input === "string" ? input : (input as Request).url;
      if (url === SENDGRID_URL) {
        const body = JSON.parse(String(init?.body)) as { personalizations: Array<{ to: unknown }>; subject: string; content: Array<{ value: string }> };
        captured.push({ to: body.personalizations[0]?.to, subject: body.subject, text: body.content[0]?.value ?? "" });
        return new Response(null, { status: 202 });
      }
      throw new Error(`unexpected fetch in assistant-latency-alert test: ${url}`);
    });
    try {
      const now = Math.floor(Date.now() / 1000);
      await seedSamples(now, [16000, 17000, 35000, 15000, 16500]); // one real stall at 35s
      await freshRun({ SEND_APPROVED_PASSES: "assistantLatencyAlert", SENDGRID_API_KEY: "test-key" });
      expect(captured).toHaveLength(1);
      expect(captured[0]?.subject).toContain("assistant chat latency degraded");
      expect(captured[0]?.text).toContain("35.0s");
      expect(captured[0]?.text).toContain("~15-18s"); // documents the known baseline, doesn't just say "slow"
    } finally {
      fetchSpy.mockRestore();
    }
  });

  it("MON-4 end-to-end: a 429 storm alongside a real breach still fires -- proves the dilution bug is actually fixed at the pass level, not just in the store function", async () => {
    const captured: string[] = [];
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = typeof input === "string" ? input : (input as Request).url;
      if (url === SENDGRID_URL) {
        const body = JSON.parse(String(init?.body)) as { subject: string };
        captured.push(body.subject);
        return new Response(null, { status: 202 });
      }
      throw new Error(`unexpected fetch: ${url}`);
    });
    try {
      const now = Math.floor(Date.now() / 1000);
      // AuditLab's exact simulation shape: a rate-limit storm would drag an
      // all-rows p95 down to ~0.4s (breach=false) if MON-4 weren't fixed.
      await seedSamples(now, Array.from({ length: 950 }, () => 400), "rate_limited");
      await seedSamples(now, Array.from({ length: 50 }, () => 26000), "success");
      await freshRun({ SEND_APPROVED_PASSES: "assistantLatencyAlert", SENDGRID_API_KEY: "test-key" });
      expect(captured).toHaveLength(1);
    } finally {
      fetchSpy.mockRestore();
    }
  });

  it("approved, p95 threshold breached without any single sample over 30s -- still sends", async () => {
    const captured: string[] = [];
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = typeof input === "string" ? input : (input as Request).url;
      if (url === SENDGRID_URL) {
        const body = JSON.parse(String(init?.body)) as { subject: string };
        captured.push(body.subject);
        return new Response(null, { status: 202 });
      }
      throw new Error(`unexpected fetch: ${url}`);
    });
    try {
      const now = Math.floor(Date.now() / 1000);
      // 20 samples all at 26s -- p95 (19th of 20) = 26000 > 25000 threshold,
      // but max (26000) is under the 30000 max threshold. Isolates the p95
      // branch from the max branch.
      await seedSamples(now, Array.from({ length: 20 }, () => 26000));
      await freshRun({ SEND_APPROVED_PASSES: "assistantLatencyAlert", SENDGRID_API_KEY: "test-key" });
      expect(captured).toHaveLength(1);
    } finally {
      fetchSpy.mockRestore();
    }
  });

  it("a breach sends at most once per UTC day -- a second call the same day is a no-op", async () => {
    let sendCount = 0;
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = typeof input === "string" ? input : (input as Request).url;
      if (url === SENDGRID_URL) {
        sendCount++;
        return new Response(null, { status: 202 });
      }
      throw new Error(`unexpected fetch: ${url}`);
    });
    try {
      const now = Math.floor(Date.now() / 1000);
      await seedSamples(now, [40000]);
      const runOnce = async () => {
        const { runAssistantLatencyAlertPass } = await import("../src/scheduler");
        return runAssistantLatencyAlertPass({ ...env, SEND_APPROVED_PASSES: "assistantLatencyAlert", SENDGRID_API_KEY: "test-key" } as never);
      };
      await runOnce();
      await store.logAssistantChatLatency(env.DB, 41000, now, "success"); // still breaching on a later tick
      await runOnce();
      expect(sendCount).toBe(1);
    } finally {
      fetchSpy.mockRestore();
    }
  });

  it("no SENDGRID_API_KEY -- degrades safely, no fetch attempted, no throw", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    try {
      const now = Math.floor(Date.now() / 1000);
      await seedSamples(now, [50000]);
      await expect(
        freshRun({ SEND_APPROVED_PASSES: "assistantLatencyAlert", SENDGRID_API_KEY: undefined })
      ).resolves.toBeUndefined();
      expect(fetchSpy).not.toHaveBeenCalled();
    } finally {
      fetchSpy.mockRestore();
    }
  });

  it("a SendGrid failure (non-202) unclaims the day, so a later tick can retry rather than losing the alert silently", async () => {
    let calls = 0;
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = typeof input === "string" ? input : (input as Request).url;
      if (url === SENDGRID_URL) {
        calls++;
        return new Response("simulated failure", { status: 500 });
      }
      throw new Error(`unexpected fetch: ${url}`);
    });
    try {
      const now = Math.floor(Date.now() / 1000);
      await seedSamples(now, [45000]);
      const dayUtc = new Date().toISOString().slice(0, 10);
      const { runAssistantLatencyAlertPass } = await import("../src/scheduler");
      // MON-5: inject a no-op sleep so the retry backoff doesn't add real
      // seconds to the suite; the real fetch mock still exercises
      // sendViaSendGrid's [sendgrid-fail] status+body logging path 3x.
      await runAssistantLatencyAlertPass(
        { ...env, SEND_APPROVED_PASSES: "assistantLatencyAlert", SENDGRID_API_KEY: "test-key" } as never,
        { sleep: async () => {} }
      );
      // MON-5: the send is retried to exhaustion before giving up (was 1 call).
      expect(calls).toBe(3);
      // The failed send must NOT have burned today's claim -- a fresh claim
      // attempt should still succeed (DROP-3-shaped unclaim-on-failure).
      expect(await store.claimAssistantLatencyAlertForToday(env.DB, dayUtc)).toBe(true);
    } finally {
      fetchSpy.mockRestore();
    }
  });

  // MON-5 (2026-09-02): retry/backoff -- a single transient SendGrid 4xx used
  // to drop the whole day's only alert. These use the pass's `send`/`sleep`
  // injection seam so they're deterministic and don't burn real backoff time.
  it("MON-5: retries and SUCCEEDS on a later attempt -- a transient failure does not lose the alert, and the day stays claimed", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch"); // must never hit the network -- send is injected
    try {
      const now = Math.floor(Date.now() / 1000);
      await seedSamples(now, [45000]);
      const dayUtc = new Date().toISOString().slice(0, 10);
      let attempts = 0;
      const { runAssistantLatencyAlertPass } = await import("../src/scheduler");
      await runAssistantLatencyAlertPass(
        { ...env, SEND_APPROVED_PASSES: "assistantLatencyAlert", SENDGRID_API_KEY: "test-key" } as never,
        {
          send: async () => {
            attempts++;
            return attempts >= 3; // fail, fail, then succeed
          },
          sleep: async () => {},
        }
      );
      expect(attempts).toBe(3);
      expect(fetchSpy).not.toHaveBeenCalled();
      // A success on the 3rd attempt KEEPS the day claim -> a re-claim fails.
      expect(await store.claimAssistantLatencyAlertForToday(env.DB, dayUtc)).toBe(false);
    } finally {
      fetchSpy.mockRestore();
    }
  });

  it("MON-5: all attempts fail -- send is tried exactly ATTEMPTS times, then the day is unclaimed for a later retry (no throw)", async () => {
    const now = Math.floor(Date.now() / 1000);
    await seedSamples(now, [45000]);
    const dayUtc = new Date().toISOString().slice(0, 10);
    let attempts = 0;
    const { runAssistantLatencyAlertPass } = await import("../src/scheduler");
    await expect(
      runAssistantLatencyAlertPass(
        { ...env, SEND_APPROVED_PASSES: "assistantLatencyAlert", SENDGRID_API_KEY: "test-key" } as never,
        { send: async () => { attempts++; return false; }, sleep: async () => {} }
      )
    ).resolves.toBeUndefined();
    expect(attempts).toBe(3);
    expect(await store.claimAssistantLatencyAlertForToday(env.DB, dayUtc)).toBe(true); // released
  });

  it("MON-5: a first-attempt success does NOT retry and does NOT back off", async () => {
    const now = Math.floor(Date.now() / 1000);
    await seedSamples(now, [45000]);
    let attempts = 0;
    const sleeps: number[] = [];
    const { runAssistantLatencyAlertPass } = await import("../src/scheduler");
    await runAssistantLatencyAlertPass(
      { ...env, SEND_APPROVED_PASSES: "assistantLatencyAlert", SENDGRID_API_KEY: "test-key" } as never,
      { send: async () => { attempts++; return true; }, sleep: async (ms) => { sleeps.push(ms); } }
    );
    expect(attempts).toBe(1);
    expect(sleeps).toEqual([]);
  });

  it("MON-5: backoff grows between attempts (2s then 4s) and there are exactly ATTEMPTS-1 waits", async () => {
    const now = Math.floor(Date.now() / 1000);
    await seedSamples(now, [45000]);
    const sleeps: number[] = [];
    const { runAssistantLatencyAlertPass } = await import("../src/scheduler");
    await runAssistantLatencyAlertPass(
      { ...env, SEND_APPROVED_PASSES: "assistantLatencyAlert", SENDGRID_API_KEY: "test-key" } as never,
      { send: async () => false, sleep: async (ms) => { sleeps.push(ms); } }
    );
    // 3 attempts -> 2 backoffs, increasing: BACKOFF_MS*1, BACKOFF_MS*2.
    expect(sleeps).toEqual([2000, 4000]);
  });
});
