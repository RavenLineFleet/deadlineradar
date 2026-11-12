/**
 * SRC-228 (AuditLab, MEDIUM) -- the acceptance test for SRC-226.
 *
 * scheduled() used `return` on a US federal holiday right before the reminder
 * pass, so every pass registered after it (drip course, rule-change alerts,
 * digest, compliance newsletter, trial-ending notice/alert, roster-pause
 * reconciliation, Stripe price parity, the staleness/latency/error alerts)
 * was silently skipped too. A trialing firm whose trial ends on Thanksgiving
 * got no trial-ending mail that day. Only the deadline-reminder pass is meant
 * to skip (holidays.ts: it catches up the next day).
 *
 * The test pins Date to Thanksgiving 2026 and to the day before, runs
 * scheduled() on both, and asserts:
 *   - on the holiday the reminder pass is skipped and logs that it was;
 *   - every other pass registers on the holiday exactly as on the plain day
 *     (waitUntil count is one less, the reminder pass, and nothing else);
 *   - every pass that logs a tagged line on the plain day logs it on the
 *     holiday too, except the reminder pass's summary.
 */
import { env } from "cloudflare:test";
import { describe, expect, it, vi } from "vitest";

const HOLIDAY = new Date("2026-11-26T18:00:00Z"); // Thanksgiving, the cron's 18:00 UTC slot
const PLAIN_DAY = new Date("2026-11-25T18:00:00Z");

async function runScheduledAt(when: Date): Promise<{ logs: string[]; waitUntilCount: number }> {
  const worker = (await import("../src/index")).default;
  const logs: string[] = [];
  const logSpy = vi.spyOn(console, "log").mockImplementation((msg: unknown) => {
    logs.push(String(msg));
  });
  const waited: Promise<unknown>[] = [];
  const ctx = { waitUntil: (p: Promise<unknown>) => waited.push(p) } as unknown as ExecutionContext;
  const envWithKey = { ...env, RESEND_API_KEY: "test-key-not-real" };
  vi.useFakeTimers({ toFake: ["Date"] });
  try {
    vi.setSystemTime(when);
    await worker.scheduled({} as ScheduledController, envWithKey, ctx);
    await Promise.allSettled(waited);
  } finally {
    vi.useRealTimers();
    logSpy.mockRestore();
  }
  return { logs, waitUntilCount: waited.length };
}

function tags(logs: string[]): Set<string> {
  const out = new Set<string>();
  for (const l of logs) {
    const m = /^\[([a-z0-9-]+-cron)\]/.exec(l);
    if (m) out.add(m[1]!);
  }
  return out;
}

describe("SRC-228/SRC-226: a federal holiday skips only the reminder pass", () => {
  it("the dates really are a holiday and a plain day", async () => {
    const { isUsFederalHoliday } = await import("../src/holidays");
    expect(isUsFederalHoliday(HOLIDAY)).toBe(true);
    expect(isUsFederalHoliday(PLAIN_DAY)).toBe(false);
  });

  it("on Thanksgiving every non-reminder pass still runs", async () => {
    const plain = await runScheduledAt(PLAIN_DAY);
    const holiday = await runScheduledAt(HOLIDAY);

    expect(holiday.logs).toContain("[reminder-cron] skipped -- US federal holiday");
    expect(plain.logs).not.toContain("[reminder-cron] skipped -- US federal holiday");

    // Exactly one fewer registered pass: the reminder pass, nothing after it.
    expect(holiday.waitUntilCount).toBe(plain.waitUntilCount - 1);

    const plainTags = tags(plain.logs);
    const holidayTags = tags(holiday.logs);
    for (const t of [
      "drip-course-cron",
      "rule-change-alert-cron",
      "digest-cron",
      "compliance-newsletter-cron",
    ]) {
      expect(plainTags.has(t), `${t} logs on a plain day`).toBe(true);
      expect(holidayTags.has(t), `${t} must still run on a holiday`).toBe(true);
    }
    for (const t of plainTags) {
      if (t === "reminder-cron") continue;
      expect(holidayTags.has(t), `${t} ran on the plain day but not on the holiday`).toBe(true);
    }
    // The reminder pass's own summary line never appears on the holiday.
    expect(holiday.logs.some((l) => l.startsWith("[reminder-cron] {"))).toBe(false);
  });
});
