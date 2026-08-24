/**
 * Orchestrator walkthrough finding (2026-08-24): `/for-firms/` shows a
 * "not a mockup" screenshot of a populated 5-staff demo roster, but the
 * shared demo firm is deliberately left mutable for visitor exploration
 * (roster add/remove is the one class of mutation `demo_locked` never
 * gates), so it can drift to empty and contradict the marketing page.
 * `store.reseedDemoFirmRosterIfBelowFloor()` is the standing baseline fix,
 * called every day from `scheduled()` -- these tests exercise the
 * store-layer function directly, same level as the other `demo-*.spec.ts`
 * files' own `store.` calls.
 */
import { env } from "cloudflare:test";
import { beforeEach, describe, expect, it } from "vitest";
import * as store from "../src/store";

// Same cross-test-pollution reasoning as demo-login.spec.ts's own
// beforeEach: getDemoFirm() is a bare `WHERE demo_locked = 1` with no other
// filter, and storage persists across tests within this file. ALSO deletes
// every subscriber row: findActiveOrPending() (used by the reseed function
// to avoid duplicating an already-present staffer) is scoped by
// (cooldown_key, state_slug), NOT by firm_id -- fine in production (there
// is only ever one demo_locked=1 firm), but this file's fixed baseline
// emails would otherwise collide across different tests' different firm
// ids and make a later test's reseed silently skip members an EARLIER
// test already claimed for its own, unrelated firm.
beforeEach(async () => {
  await env.DB.prepare(`UPDATE firms SET demo_locked = 0 WHERE demo_locked = 1`).run();
  await env.DB.prepare(`DELETE FROM subscribers`).run();
  await env.DB.prepare(`DELETE FROM activity_log`).run();
});

async function makeDemoFirm(label: string): Promise<string> {
  const { id } = await store.createFirm(env.DB, {
    name: "Demo Roster Reseed Test LLC",
    adminEmail: `${label}-${Date.now()}-${Math.floor(performance.now())}@examplefirm.com`,
  });
  await env.DB.prepare(`UPDATE firms SET demo_locked = 1 WHERE id = ?1`).bind(id).run();
  return id;
}

describe("reseedDemoFirmRosterIfBelowFloor", () => {
  it("no-ops when there is no demo firm at all", async () => {
    const result = await store.reseedDemoFirmRosterIfBelowFloor(env.DB);
    expect(result).toEqual({ seeded: false, count: 0 });
  });

  it("seeds a baseline roster when the demo firm's roster is empty", async () => {
    const firmId = await makeDemoFirm("empty");
    expect(await store.countFirmLicenses(env.DB, firmId)).toBe(0);

    const result = await store.reseedDemoFirmRosterIfBelowFloor(env.DB);

    expect(result.seeded).toBe(true);
    expect(result.count).toBeGreaterThanOrEqual(5);
    const rows = await store.listFirmLicenses(env.DB, firmId);
    expect(rows.length).toBeGreaterThanOrEqual(5);
    // Reinstates the two names the orchestrator's walkthrough found already
    // referenced (as orphaned rows) in this account's own activity_log --
    // see the store function's own docstring for why these two specifically.
    const emails = rows.map((r) => r.email);
    expect(emails).toContain("jordan.mitchell@demo.deadline-radar.com");
    expect(emails).toContain("morgan.patel@demo.deadline-radar.com");
    // Every seeded row must actually be able to compute a real deadline --
    // an unresolvable license_type_id would silently render as "not
    // computed" on the dashboard, the same kind of quiet defect this fix
    // exists to close.
    for (const row of rows) {
      const fields = JSON.parse(row.deadline_fields) as Record<string, string>;
      expect(fields.license_type_id).toBeTruthy();
    }
  });

  it("does NOT reseed (and does not duplicate) when the roster is already at or above the floor", async () => {
    const firmId = await makeDemoFirm("populated");
    await store.addPending(env.DB, {
      email: "existing.one@demo.deadline-radar.com",
      stateSlug: "illinois",
      deadlineFields: { license_type_id: "il-individual" },
      firstName: "Existing",
      firmId,
      skipConfirmation: true,
    });
    await store.addPending(env.DB, {
      email: "existing.two@demo.deadline-radar.com",
      stateSlug: "georgia",
      deadlineFields: { license_type_id: "ga-individual" },
      firstName: "Existing",
      firmId,
      skipConfirmation: true,
    });
    expect(await store.countFirmLicenses(env.DB, firmId)).toBe(2);

    const result = await store.reseedDemoFirmRosterIfBelowFloor(env.DB);

    expect(result).toEqual({ seeded: false, count: 2 });
    expect(await store.countFirmLicenses(env.DB, firmId)).toBe(2);
  });

  it("only backfills the missing baseline members when SOME staff remain, never duplicates the ones already present", async () => {
    const firmId = await makeDemoFirm("partial");
    // Simulates a visitor removing all but one baseline staffer -- below
    // the floor, so a reseed pass fires, but must not create a second row
    // for the one that's already there.
    await store.addPending(env.DB, {
      email: "jordan.mitchell@demo.deadline-radar.com",
      stateSlug: "illinois",
      deadlineFields: { license_type_id: "il-individual" },
      firstName: "Jordan",
      firmId,
      skipConfirmation: true,
    });
    expect(await store.countFirmLicenses(env.DB, firmId)).toBe(1);

    const result = await store.reseedDemoFirmRosterIfBelowFloor(env.DB);

    expect(result.seeded).toBe(true);
    const rows = await store.listFirmLicenses(env.DB, firmId);
    const jordanRows = rows.filter((r) => r.email === "jordan.mitchell@demo.deadline-radar.com");
    expect(jordanRows.length).toBe(1);
    expect(rows.length).toBeGreaterThanOrEqual(5);
  });

  it("logs an activity_log entry for each newly-seeded staffer, same as a real admin add", async () => {
    const firmId = await makeDemoFirm("activity");
    await store.reseedDemoFirmRosterIfBelowFloor(env.DB);
    const activity = await store.listRecentActivity(env.DB, firmId, 20);
    const addedEmails = activity.filter((a) => a.event_type === "added").map((a) => a.email);
    expect(addedEmails).toContain("jordan.mitchell@demo.deadline-radar.com");
    expect(addedEmails).toContain("morgan.patel@demo.deadline-radar.com");
  });
});
