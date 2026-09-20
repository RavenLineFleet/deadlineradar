/**
 * AuditLab COPY-18 (MEDIUM, 2026-09-20): reminderSubject()'s trailing
 * clause ("-- a good time to start" / "expires") was keyed off `threshold`
 * (which tier fired) instead of `actual` (the true remaining day count),
 * so an overdue subscriber under a narrowed reminder_thresholds set (one
 * that excludes 1 -- e.g. [30] alone) could get a subject line telling
 * them it's "a good time to start" days after their deadline already
 * passed. Reachable for any firm whose thresholds' minimum is 7/14/30/60;
 * the default set is safe (minimum is always 1). A positive-days-only test
 * suite would pass on the old code and not catch a regression here, so
 * this file crosses every `actual` in the band AuditLab specified against
 * every real threshold, not just the reachable combinations.
 */
import { describe, expect, it } from "vitest";

const ALL_THRESHOLDS = [1, 3, 7, 14, 30, 60] as const;
const ACTUAL_BAND = [-14, -4, -3, -1, 0, 1] as const;

async function subjectFor(threshold: number, actual: number): Promise<string> {
  const { buildReminderEmail } = await import("../src/emails");
  const built = buildReminderEmail(
    "Texas",
    "2026-09-06",
    threshold,
    actual,
    "https://example.com/next",
    "https://example.com/stop",
    "https://example.com/unsub"
  );
  return built.subject;
}

describe("AuditLab COPY-18: reminder subject line keys off actual, not threshold, once overdue", () => {
  it.each(ACTUAL_BAND.flatMap((actual) => ALL_THRESHOLDS.map((threshold) => [actual, threshold] as const)))(
    "actual=%i threshold=%i never claims a positive-framing tail once overdue",
    async (actual, threshold) => {
      const subject = await subjectFor(threshold, actual);
      if (actual <= 0) {
        // The bug's exact symptom: once the real deadline has passed (or is
        // today), no subject may say the forward-looking "a good time to
        // start", and the 60-tier's "expires" must not read present-tense
        // for a date that's already gone.
        expect(subject).not.toContain("a good time to start");
        expect(subject).not.toMatch(/\bexpires\b.*\bago\b/);
      }
    }
  );

  it("actual=-14 threshold=30 (the exact case AuditLab reproduced) says Overdue, not 'a good time to start'", async () => {
    const subject = await subjectFor(30, -14);
    expect(subject).toBe("Overdue: your Texas CPA license renewal is due (2026-09-06)");
  });

  it("actual=-14 threshold=60 says Overdue, not present-tense 'expires ago'", async () => {
    const subject = await subjectFor(60, -14);
    expect(subject).toBe("Overdue: your Texas CPA license renewal is due (2026-09-06)");
  });

  it("actual=0 threshold=7 says Today, not 'a good time to start'", async () => {
    const subject = await subjectFor(7, 0);
    expect(subject).toBe("Today: your Texas CPA license renewal is due (2026-09-06)");
  });

  it("actual=1 threshold=1 says Tomorrow (the fix's `actual <= 0` gate correctly excludes actual=1)", async () => {
    const subject = await subjectFor(1, 1);
    expect(subject).toBe("Tomorrow: your Texas CPA license renewal is due (2026-09-06)");
  });

  it("actual=1 threshold=14 is still forward-looking (not overdue), so it correctly keeps 'a good time to start'", async () => {
    const subject = await subjectFor(14, 1);
    expect(subject).toBe("Your Texas CPA license renewal is due in 1 day (2026-09-06) — a good time to start");
  });

  // Positive-days control: confirms the fix's `actual <= 0` gate doesn't
  // accidentally widen and swallow the genuinely correct forward-looking
  // cases too.
  it("actual=14 threshold=14 (still forward-looking, unaffected by the fix) keeps 'a good time to start'", async () => {
    const subject = await subjectFor(14, 14);
    expect(subject).toBe("Your Texas CPA license renewal is due in 14 days (2026-09-06) — a good time to start");
  });

  it("actual=30 threshold=60 (still forward-looking) keeps present-tense 'expires'", async () => {
    const subject = await subjectFor(60, 30);
    expect(subject).toBe("Your Texas CPA license expires in 30 days (2026-09-06)");
  });
});
