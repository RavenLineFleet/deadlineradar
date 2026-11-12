import { vi } from "vitest";
import { isUsFederalHoliday } from "../src/holidays";

// scheduled() skips the reminder pass on a US federal holiday (index.ts holiday
// guard). Before SRC-226 it skipped every later pass too, so wiring tests that
// assert a pass ran through scheduled() failed whenever the suite ran on a
// holiday (first seen Veterans Day 2026-11-11, blocking a deploy); tests about
// the reminder pass still need a non-holiday clock. This moves only the
// clock, by whole days, to the next non-holiday, so data-freshness guards still
// see a date within a day or two of real time.
export async function withNonHolidayClock<T>(fn: () => Promise<T>): Promise<T> {
  // SRC-229: compare against the captured start, not a second Date.now() --
  // the clock can tick between the two reads and send a non-holiday run
  // through the fake-timer branch for nothing.
  const start = Date.now();
  let t = start;
  while (isUsFederalHoliday(new Date(t))) t += 86_400_000;
  if (t === start) return fn();
  vi.useFakeTimers({ toFake: ["Date"] });
  try {
    vi.setSystemTime(new Date(t));
    return await fn();
  } finally {
    vi.useRealTimers();
  }
}
