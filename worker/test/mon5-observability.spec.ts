/**
 * MON-5 (2026-09-02, AuditLab MEDIUM, Devin-approved): the assistant-latency
 * alert failed silently on 2026-09-02 (a SendGrid 4xx that unclaimed its own
 * day row and logged nothing), and nothing recorded that the nightly cron
 * even fired. This file covers the two observability halves of the fix:
 *   1. sendViaSendGrid() now LOGS status+body on a non-2xx (and names a
 *      thrown/aborted send) instead of returning a bare `false`.
 *   2. scheduled() writes an unconditional cron-liveness heartbeat, proven
 *      here to be actually wired into the entrypoint (not just unit-correct).
 * (Part 3, retry/backoff, lives in assistant-latency-alert.spec.ts.)
 */
import { env } from "cloudflare:test";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { sendViaSendGrid } from "../src/sender";
import type { BuiltEmail } from "../src/emails";
import * as store from "../src/store";

const SENDGRID_URL = "https://api.sendgrid.com/v3/mail/send";

function fakeEmail(): BuiltEmail {
  return { subject: "s", textBody: "t", htmlBody: "<p>t</p>", headers: {} };
}

describe("MON-5 part 1: sendViaSendGrid() surfaces the failure instead of swallowing it", () => {
  it("a non-2xx logs [sendgrid-fail] with the status AND a body snippet, and still returns false", async () => {
    const logs: string[] = [];
    const logSpy = vi.spyOn(console, "log").mockImplementation((m: unknown) => { logs.push(String(m)); });
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = typeof input === "string" ? input : (input as Request).url;
      if (url === SENDGRID_URL) {
        return new Response('{"errors":[{"message":"The from address does not match a verified Sender Identity"}]}', { status: 403 });
      }
      throw new Error(`unexpected fetch: ${url}`);
    });
    try {
      // EMAIL_ALLOWLIST unset -> no short-circuit -> reaches fetch.
      const result = await sendViaSendGrid("fake-api-key", "support@deadline-radar.com", fakeEmail(), undefined);
      expect(result).toBe(false);
      const line = logs.find((l) => l.includes("[sendgrid-fail]"));
      expect(line).toBeDefined();
      expect(line).toContain("status=403");
      expect(line).toContain("verified Sender Identity"); // the body reason is captured, not discarded
    } finally {
      fetchSpy.mockRestore();
      logSpy.mockRestore();
    }
  });

  it("a thrown fetch (network failure / timeout abort) logs [sendgrid-error] and returns false, never throwing", async () => {
    const logs: string[] = [];
    const logSpy = vi.spyOn(console, "log").mockImplementation((m: unknown) => { logs.push(String(m)); });
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("network down"));
    try {
      const result = await sendViaSendGrid("fake-api-key", "support@deadline-radar.com", fakeEmail(), undefined);
      expect(result).toBe(false);
      expect(logs.some((l) => l.includes("[sendgrid-error]") && l.includes("network down"))).toBe(true);
    } finally {
      fetchSpy.mockRestore();
      logSpy.mockRestore();
    }
  });

  it("a 2xx logs NOTHING and returns true (the happy path is unchanged and quiet)", async () => {
    const logs: string[] = [];
    const logSpy = vi.spyOn(console, "log").mockImplementation((m: unknown) => { logs.push(String(m)); });
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = typeof input === "string" ? input : (input as Request).url;
      if (url === SENDGRID_URL) return new Response(null, { status: 202 });
      throw new Error(`unexpected fetch: ${url}`);
    });
    try {
      const result = await sendViaSendGrid("fake-api-key", "support@deadline-radar.com", fakeEmail(), undefined);
      expect(result).toBe(true);
      expect(logs.some((l) => l.includes("[sendgrid-"))).toBe(false);
    } finally {
      fetchSpy.mockRestore();
      logSpy.mockRestore();
    }
  });
});

describe("MON-5 part 2: cron-liveness heartbeat (store unit)", () => {
  beforeEach(async () => {
    await env.DB.prepare("DELETE FROM scheduler_heartbeat").run();
  });

  it("getCronHeartbeat is null before the tick ever runs", async () => {
    expect(await store.getCronHeartbeat(env.DB)).toBeNull();
  });

  it("recordCronHeartbeat inserts row id=1 (run_count 1), then UPSERTs (run_count increments, last_run_at advances)", async () => {
    await store.recordCronHeartbeat(env.DB);
    const first = await store.getCronHeartbeat(env.DB);
    expect(first?.run_count).toBe(1);
    expect(typeof first?.last_run_at).toBe("string");

    await store.recordCronHeartbeat(env.DB);
    const second = await store.getCronHeartbeat(env.DB);
    expect(second?.run_count).toBe(2);

    // Exactly one row ever (single-row CHECK id=1, never grows).
    const count = await env.DB.prepare("SELECT COUNT(*) AS n FROM scheduler_heartbeat").first<{ n: number }>();
    expect(count?.n).toBe(1);
  });

  it("the UPSERT path rewrites last_run_at (a stale timestamp with a growing run_count would be a lying heartbeat)", async () => {
    // Fake clock so the two ticks are deterministically 1 hour apart; with the
    // real clock they can land in the same millisecond and this would flake.
    vi.useFakeTimers();
    try {
      vi.setSystemTime(new Date("2026-09-03T18:00:00.000Z"));
      await store.recordCronHeartbeat(env.DB);
      const first = await store.getCronHeartbeat(env.DB);
      expect(first?.last_run_at).toBe("2026-09-03T18:00:00.000Z");

      vi.setSystemTime(new Date("2026-09-03T19:00:00.000Z"));
      await store.recordCronHeartbeat(env.DB);
      const second = await store.getCronHeartbeat(env.DB);
      expect(second?.run_count).toBe(2);
      expect(second?.last_run_at).toBe("2026-09-03T19:00:00.000Z");
      expect(second?.last_run_at).not.toBe(first?.last_run_at);
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("MON-5 part 2: the heartbeat is actually WIRED into scheduled()", () => {
  beforeEach(async () => {
    await env.DB.prepare("DELETE FROM scheduler_heartbeat").run();
  });

  it("invoking the real Worker cron entrypoint writes/advances the heartbeat, unconditionally", async () => {
    const worker = (await import("../src/index")).default;
    const logSpy = vi.spyOn(console, "log").mockImplementation(() => {});
    const waited: Promise<unknown>[] = [];
    const ctx = { waitUntil: (p: Promise<unknown>) => waited.push(p) } as unknown as ExecutionContext;
    // No SEND_APPROVED_PASSES + a fake key: every send-gated pass no-ops, so
    // this exercises the entrypoint's own heartbeat write, not the passes.
    const scheduledEnv = { ...env, SENDGRID_API_KEY: "test-key-not-real" };
    try {
      expect(await store.getCronHeartbeat(env.DB)).toBeNull();

      await worker.scheduled({} as ScheduledController, scheduledEnv as never, ctx);
      const after1 = await store.getCronHeartbeat(env.DB);
      expect(after1?.run_count).toBe(1);
      expect(after1?.last_run_at).toBeTruthy();

      await worker.scheduled({} as ScheduledController, scheduledEnv as never, ctx);
      const after2 = await store.getCronHeartbeat(env.DB);
      expect(after2?.run_count).toBe(2);

      await Promise.allSettled(waited); // drain dispatched passes, ignore their outcomes
    } finally {
      logSpy.mockRestore();
    }
  });
});
