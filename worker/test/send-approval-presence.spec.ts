/**
 * SecurityLab 2026-10-09: SEND_APPROVED_PASSES is write-only; the cron reports
 * which KNOWN passes are approved (intersection, names only), never the raw
 * value and never an unrecognised token.
 */
import { env } from "cloudflare:test";
import { afterEach, describe, expect, it, vi } from "vitest";
import { KNOWN_SEND_PASSES, approvedKnownPasses } from "../src/scheduler";

function ctx(): ExecutionContext {
  return { waitUntil() {}, passThroughOnException() {}, props: {} } as unknown as ExecutionContext;
}
async function runCron(overrides: Record<string, unknown>): Promise<void> {
  const worker = (await import("../src/index")).default;
  await worker.scheduled({} as ScheduledController, { ...env, ...overrides } as never, ctx());
}
async function presenceRows(): Promise<string[]> {
  const r = await env.DB.prepare("SELECT event_type FROM stripe_webhook_events WHERE id LIKE 'send_approval_presence:%'").all<{ event_type: string }>();
  return r.results.map((x) => x.event_type);
}

afterEach(() => vi.restoreAllMocks());

describe("approvedKnownPasses", () => {
  it("returns exactly the known passes present in the value, ignoring whitespace", () => {
    const e = { ...env, SEND_APPROVED_PASSES: " billingSyncAlert , trialEndingAlert " } as never;
    expect(approvedKnownPasses(e)).toEqual(["billingSyncAlert", "trialEndingAlert"]);
  });
  it("unset / empty => none (control: fails closed like requireSendApproval)", () => {
    expect(approvedKnownPasses({ ...env, SEND_APPROVED_PASSES: undefined } as never)).toEqual([]);
    expect(approvedKnownPasses({ ...env, SEND_APPROVED_PASSES: "" } as never)).toEqual([]);
  });
  it("an unrecognised or mistyped token is never reported", () => {
    const e = { ...env, SEND_APPROVED_PASSES: "billingSyncAlert,sekrit_typo_token,billingsyncalert" } as never;
    expect(approvedKnownPasses(e)).toEqual(["billingSyncAlert"]);
  });
  it("lists nine distinct names", () => {
    expect(new Set(KNOWN_SEND_PASSES).size).toBe(9);
  });
});

describe("scheduled() presence diagnostic", () => {
  it("writes one bounded row and a log line; neither contains a stray token or the raw value", async () => {
    await env.DB.prepare("DELETE FROM stripe_webhook_events WHERE id LIKE 'send_approval_presence:%'").run();
    const log = vi.spyOn(console, "log").mockImplementation(() => {});
    const raw = "billingSyncAlert,STRAY_SECRET_TOKEN_123";
    await runCron({ SEND_APPROVED_PASSES: raw });
    const rows = await presenceRows();
    expect(rows).toHaveLength(1);
    expect(rows[0]).toContain("billingSyncAlert=1");
    expect(rows[0]).toContain("trialEndingAlert=0");
    expect(rows[0]).not.toContain("STRAY_SECRET_TOKEN_123");
    const lines = log.mock.calls.map((c) => c.map(String).join(" ")).filter((l) => l.includes("[send-approval]"));
    expect(lines).toEqual(["[send-approval] approved known passes: billingSyncAlert"]);
    expect(lines.join("\n")).not.toContain("STRAY_SECRET_TOKEN_123");
    expect(log.mock.calls.map((c) => c.map(String).join(" ")).join("\n")).not.toContain(raw);
  });
  it("is once per UTC day: a second run the same day adds no row", async () => {
    await env.DB.prepare("DELETE FROM stripe_webhook_events WHERE id LIKE 'send_approval_presence:%'").run();
    vi.spyOn(console, "log").mockImplementation(() => {});
    await runCron({ SEND_APPROVED_PASSES: "billingSyncAlert" });
    await runCron({ SEND_APPROVED_PASSES: "billingSyncAlert,trialEndingAlert" });
    expect(await presenceRows()).toHaveLength(1);
  });
  it("unset => all zeros and '(none)' in the log", async () => {
    await env.DB.prepare("DELETE FROM stripe_webhook_events WHERE id LIKE 'send_approval_presence:%'").run();
    const log = vi.spyOn(console, "log").mockImplementation(() => {});
    await runCron({ SEND_APPROVED_PASSES: undefined });
    const rows = await presenceRows();
    expect(rows[0]).not.toContain("=1");
    expect(log.mock.calls.map((c) => c.map(String).join(" "))).toContain("[send-approval] approved known passes: (none)");
  });
});
