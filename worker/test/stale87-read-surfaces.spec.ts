/**
 * AuditLab STALE-87 (MEDIUM, 2026-10-26): ST-1's freshness signal
 * (data_as_of/data_stale) reached GET /firm/licenses only. Three more read
 * surfaces serve the same computed next_deadline: GET /subscriber/licenses,
 * POST /firm/licenses/active-picks, and GET /firm/calendar.ics. Each one is
 * pinned here both fresh and stale.
 *
 * STALE_AT is found by asking dataFreshnessInfo() itself, not by
 * re-deriving its anchor, so it tracks the guard as the data is
 * re-verified. FRESH_AT sits 20 days earlier: setup (sessions, rows) happens
 * there, and a session made then is still inside its 30-day TTL at STALE_AT.
 */
import { env, SELF } from "cloudflare:test";
import { describe, expect, it, vi } from "vitest";
import * as store from "../src/store";
import { dataFreshnessInfo } from "../src/deadline";

const BASE = "https://deadline-radar.com";
const DAY_MS = 86_400_000;

function firstStaleDay(): Date {
  const start = Date.now();
  for (let d = 0; d <= 400; d++) {
    const at = new Date(start + d * DAY_MS);
    if (dataFreshnessInfo(at).stale) return at;
  }
  throw new Error("dataFreshnessInfo() never reported stale within 400 days");
}
const STALE_AT = new Date(firstStaleDay().getTime() + DAY_MS);
const FRESH_AT = new Date(STALE_AT.getTime() - 20 * DAY_MS);

function testExecutionContext(): ExecutionContext {
  return { waitUntil() {}, passThroughOnException() {}, props: {} } as unknown as ExecutionContext;
}

async function workerFetch(request: Request): Promise<Response> {
  const worker = (await import("../src/index")).default;
  return worker.fetch(request, env as never, testExecutionContext());
}

async function addRow(email: string, firmId: string | null): Promise<string> {
  const row = await store.addPending(env.DB, {
    email,
    stateSlug: "georgia",
    deadlineFields: { license_type_id: "ga-individual" },
    firstName: null,
    deadlineSource: store.DEADLINE_SOURCE_COMPUTED,
    userDeadline: null,
    firmId,
    staffLabel: null,
    skipConfirmation: true,
  });
  return row.id;
}

async function firmWithSession(name: string): Promise<{ firmId: string; cookie: string }> {
  const firm = await store.createFirm(env.DB, { name, adminEmail: `${name.replace(/\W+/g, "-").toLowerCase()}-${Date.now()}@example.com` });
  const { rawSessionToken } = await store.createSession(env.DB, firm.id);
  return { firmId: firm.id, cookie: `dr_firm_session=${rawSessionToken}` };
}

type Freshness = { data_as_of: string; data_stale: boolean };

describe("STALE-87: every read surface serving a computed deadline carries the freshness signal", () => {
  it("anchors are what they claim", () => {
    expect(dataFreshnessInfo(FRESH_AT).stale).toBe(false);
    expect(dataFreshnessInfo(STALE_AT).stale).toBe(true);
  });

  it("GET /subscriber/licenses: data_stale false when fresh, true (with data_as_of) once stale", async () => {
    vi.useFakeTimers();
    try {
      vi.setSystemTime(FRESH_AT);
      const email = `stale87-sub-${Date.now()}@example.com`;
      await addRow(email, null);
      // Subscriber sessions last 14 days, under the 20-day gap, so each
      // read gets a session minted at its own clock.
      const get = async () => {
        const { rawSessionToken } = await store.createSubscriberSession(env.DB, store.normalizeEmail(email));
        return SELF.fetch(`${BASE}/subscriber/licenses`, { headers: { Cookie: `dr_sub_session=${rawSessionToken}` } });
      };

      const fresh = (await (await get()).json()) as Freshness;
      expect(fresh.data_stale).toBe(false);
      expect(fresh.data_as_of).toBe(dataFreshnessInfo(FRESH_AT).as_of_date);

      vi.setSystemTime(STALE_AT);
      const resp = await get();
      expect(resp.status).toBe(200);
      const stale = (await resp.json()) as Freshness & { licenses: unknown[] };
      expect(stale.data_stale).toBe(true);
      expect(stale.data_as_of).toBe(dataFreshnessInfo(STALE_AT).as_of_date);
      expect(stale.licenses).toHaveLength(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it("POST /firm/licenses/active-picks: carries data_as_of/data_stale like GET /firm/licenses", async () => {
    vi.useFakeTimers();
    try {
      vi.setSystemTime(FRESH_AT);
      const { firmId, cookie } = await firmWithSession("Stale87 Picks Firm");
      const ids: string[] = [];
      for (let i = 0; i < 6; i++) ids.push(await addRow(`stale87-pick-${i}-${Date.now()}@example.com`, firmId));
      // Trial lapsed past the 7-day grace window, so the roster is over cap
      // and a pick is accepted (see pr6-roster-pause.spec.ts expireTrial()).
      await env.DB.prepare("UPDATE firms SET trial_ends_at = ?1 WHERE id = ?2")
        .bind(new Date(Date.now() - 8 * DAY_MS).toISOString(), firmId)
        .run();
      const pick = () =>
        workerFetch(
          new Request(`${BASE}/firm/licenses/active-picks`, {
            method: "POST",
            headers: { "content-type": "application/json", Cookie: cookie },
            body: JSON.stringify({ active_subscriber_ids: ids.slice(0, 3) }),
          })
        );

      const freshResp = await pick();
      expect(freshResp.status).toBe(200);
      expect(((await freshResp.json()) as Freshness).data_stale).toBe(false);

      vi.setSystemTime(STALE_AT);
      const staleResp = await pick();
      expect(staleResp.status).toBe(200);
      const stale = (await staleResp.json()) as Freshness;
      expect(stale.data_stale).toBe(true);
      expect(stale.data_as_of).toBe(dataFreshnessInfo(STALE_AT).as_of_date);
    } finally {
      vi.useRealTimers();
    }
  });

  it("GET /firm/calendar.ics: no DESCRIPTION when fresh; every event carries the caveat once stale, and no event is dropped", async () => {
    vi.useFakeTimers();
    try {
      vi.setSystemTime(FRESH_AT);
      const { firmId, cookie } = await firmWithSession("Stale87 Ics Firm");
      await addRow(`stale87-ics-a-${Date.now()}@example.com`, firmId);
      await addRow(`stale87-ics-b-${Date.now()}@example.com`, firmId);
      const get = () => SELF.fetch(`${BASE}/firm/calendar.ics`, { headers: { Cookie: cookie } });
      // Unfold RFC 5545 continuation lines before matching.
      const unfold = (ics: string) => ics.replace(/\r\n /g, "");

      const fresh = unfold(await (await get()).text());
      expect(fresh.match(/BEGIN:VEVENT/g)).toHaveLength(2);
      expect(fresh).not.toContain("DESCRIPTION:");

      vi.setSystemTime(STALE_AT);
      const resp = await get();
      expect(resp.status).toBe(200);
      const stale = unfold(await resp.text());
      expect(stale.match(/BEGIN:VEVENT/g)).toHaveLength(2);
      const descriptions = stale.match(/^DESCRIPTION:.*$/gm) ?? [];
      expect(descriptions).toHaveLength(2);
      for (const d of descriptions) {
        expect(d).toContain(`last verified ${dataFreshnessInfo(STALE_AT).as_of_date}`);
        expect(d).toContain("Confirm it with the state board");
        expect(d).not.toMatch(/REFUSING|as_of_date|last_verified/);
      }
    } finally {
      vi.useRealTimers();
    }
  });
});
