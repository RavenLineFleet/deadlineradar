import { vi } from "vitest";
import { isUsFederalHoliday } from "../src/holidays";

// scheduled() returns before its later passes on a US federal holiday
// (index.ts holiday guard, see SRC-226). Wiring tests that assert a later pass
// ran through scheduled() therefore failed whenever the suite ran on a holiday
// (first seen Veterans Day 2026-11-11, blocking a deploy). This moves only the
// clock, by whole days, to the next non-holiday, so data-freshness guards still
// see a date within a day or two of real time.
export async function withNonHolidayClock<T>(fn: () => Promise<T>): Promise<T> {
  let t = Date.now();
  while (isUsFederalHoliday(new Date(t))) t += 86_400_000;
  if (t === Date.now()) return fn();
  vi.useFakeTimers({ toFake: ["Date"] });
  try {
    vi.setSystemTime(new Date(t));
    return await fn();
  } finally {
    vi.useRealTimers();
  }
}
