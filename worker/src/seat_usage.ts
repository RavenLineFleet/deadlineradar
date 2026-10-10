/**
 * Seat usage for the staff cap (Devin's decision 2026-10-10, "option 2b").
 *
 * The cap counts PEOPLE, not roster lines:
 *   - one person licensed in several states has several rows but uses ONE seat;
 *   - firm-entity rows (license_type_id ending "-firm": the firm's own permit /
 *     registration, not a staff member) use NO seat;
 *   - admin-removed rows are excluded by the caller's query, as before.
 *
 * "Same person" = same exact normalized email (trim + lowercase). Deliberately
 * NOT store.cooldownKey(): that folds dots and "+tag" aliases, which would let
 * one firm merge many distinct staff into a single seat by giving them
 * a+jane@ / a+bob@ aliases of one inbox. Exact-email identity fails safe: a
 * person who really uses two different addresses counts as two.
 *
 * Two ceilings bound what the relaxed count could otherwise let through for
 * free (a single email cannot hold every state, a firm cannot register in
 * every state forever on the free tier):
 *   PER_PERSON_LINE_CEILING   - non-firm lines per person
 *   FIRM_ENTITY_LINE_CEILING  - firm-entity lines per firm
 * Rows already above a ceiling are never touched (freeze-at-current, same
 * grandfathering posture as the seat cap itself); the ceilings only refuse ADDS.
 *
 * Pure functions, no I/O: the add gate, tier selection at checkout, the portal
 * configuration, the over-cap emails and the roster-pause reconciler all call
 * the same code, so they cannot disagree about how many seats a roster uses.
 */

/** A generous upper bound (a judgment call, not a measured figure): well above
 * what one individual plausibly holds, while still making "one email, every
 * state, free forever" impossible (55 jurisdictions). */
export const PER_PERSON_LINE_CEILING = 10;

/** A firm's own registrations/permits. 34 jurisdictions have a "-firm" type in
 * the data; 25 is a judgment-call bound that covers a wide multi-state firm
 * without allowing the whole set on a free roster. */
export const FIRM_ENTITY_LINE_CEILING = 25;

/** One firm permit/registration type (e.g. "ga-firm") can be tracked on at most
 * this many lines: the firm's own contact plus a backup. Without it a free
 * roster could park many staff emails as "ga-firm" lines (which use no seat)
 * and never pay for them; with it, getting 25 such lines needs 13+ different
 * firm types, each only reminding about that state's FIRM deadline. */
export const FIRM_ENTITY_LINES_PER_TYPE = 2;

export interface SeatRowLike {
  email: string;
  deadline_fields: string | null;
}

export function isFirmEntityLicenseType(licenseTypeId: string | null | undefined): boolean {
  return typeof licenseTypeId === "string" && licenseTypeId.endsWith("-firm");
}

export function licenseTypeIdFromDeadlineFields(raw: string | null | undefined): string | null {
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as unknown;
    if (parsed && typeof parsed === "object") {
      const v = (parsed as Record<string, unknown>).license_type_id;
      return typeof v === "string" ? v : null;
    }
  } catch {
    // Malformed JSON: treat as a plain staff line (consumes a seat) rather
    // than throw -- a bad row must never make the cap look emptier.
  }
  return null;
}

export function personKeyForEmail(email: string): string {
  return email.trim().toLowerCase();
}

export function isFirmEntityRow(row: SeatRowLike): boolean {
  return isFirmEntityLicenseType(licenseTypeIdFromDeadlineFields(row.deadline_fields));
}

export interface SeatUsage {
  /** Distinct people with at least one non-firm-entity line: the number the cap compares against. */
  people: number;
  /** Non-firm-entity lines per person key. */
  linesByPerson: Map<string, number>;
  /** Firm-entity lines on the roster. */
  firmEntityLines: number;
  /** Firm-entity lines per license type id. */
  firmEntityByType: Map<string, number>;
}

export function computeSeatUsage(rows: readonly SeatRowLike[]): SeatUsage {
  const linesByPerson = new Map<string, number>();
  const firmEntityByType = new Map<string, number>();
  let firmEntityLines = 0;
  for (const r of rows) {
    const typeId = licenseTypeIdFromDeadlineFields(r.deadline_fields);
    if (isFirmEntityLicenseType(typeId)) {
      firmEntityLines += 1;
      firmEntityByType.set(typeId!, (firmEntityByType.get(typeId!) ?? 0) + 1);
      continue;
    }
    const key = personKeyForEmail(r.email);
    linesByPerson.set(key, (linesByPerson.get(key) ?? 0) + 1);
  }
  return { people: linesByPerson.size, linesByPerson, firmEntityLines, firmEntityByType };
}

export type AddDecision =
  | { ok: true }
  | { ok: false; reason: "seat_cap" | "person_line_ceiling" | "firm_entity_ceiling" | "firm_entity_type_ceiling" };

/**
 * Whether adding ONE more line (this email, this license type) to a roster is
 * allowed under `seatCap`. Existing over-cap rosters are frozen, not purged:
 * the only thing refused is an add that would take a NEW person past the cap.
 */
export function decideAdd(
  usage: SeatUsage,
  email: string,
  licenseTypeId: string | null | undefined,
  seatCap: number
): AddDecision {
  if (isFirmEntityLicenseType(licenseTypeId)) {
    if (usage.firmEntityLines >= FIRM_ENTITY_LINE_CEILING) return { ok: false, reason: "firm_entity_ceiling" };
    if ((usage.firmEntityByType.get(licenseTypeId!) ?? 0) >= FIRM_ENTITY_LINES_PER_TYPE) {
      return { ok: false, reason: "firm_entity_type_ceiling" };
    }
    return { ok: true };
  }
  const key = personKeyForEmail(email);
  const existingLines = usage.linesByPerson.get(key) ?? 0;
  if (existingLines >= PER_PERSON_LINE_CEILING) return { ok: false, reason: "person_line_ceiling" };
  if (existingLines === 0 && usage.people >= seatCap) return { ok: false, reason: "seat_cap" };
  return { ok: true };
}

export interface SeatRowOrdered extends SeatRowLike {
  id: string;
  created_at: string;
}

function byAge(a: SeatRowOrdered, b: SeatRowOrdered): number {
  if (a.created_at !== b.created_at) return a.created_at < b.created_at ? -1 : 1;
  return a.id < b.id ? -1 : a.id > b.id ? 1 : 0;
}

/**
 * Post-INSERT re-check for the add gate (SecurityLab SEAT-1, 2026-10-10).
 * The gate reads the roster, decides, then inserts, with nothing serialising
 * those steps, so two concurrent adds can both pass. After the insert the
 * handler re-reads the roster and asks THIS function whether the row it just
 * wrote is within the limits. The decision is deterministic and shared by
 * every racer: rows are ranked by (created_at, id), so when two adds raced
 * past the cap exactly the later-ranked one is told to undo itself, and the
 * earlier one stands. It reuses the same constants as decideAdd(), so the
 * rules still live only in this file.
 *
 * Only ever flags the row the caller just added: an existing person's extra
 * state line is never the "new person", and a roster that was already over the
 * cap (frozen, grandfathered) is not retroactively touched.
 */
export function addViolatesAfterInsert(rows: readonly SeatRowOrdered[], added: SeatRowOrdered, seatCap: number): AddDecision {
  const typeId = licenseTypeIdFromDeadlineFields(added.deadline_fields);
  if (isFirmEntityLicenseType(typeId)) {
    const firmRows = rows.filter((r) => isFirmEntityRow(r)).sort(byAge);
    if (firmRows.findIndex((r) => r.id === added.id) >= FIRM_ENTITY_LINE_CEILING) {
      return { ok: false, reason: "firm_entity_ceiling" };
    }
    const sameType = firmRows.filter((r) => licenseTypeIdFromDeadlineFields(r.deadline_fields) === typeId);
    if (sameType.findIndex((r) => r.id === added.id) >= FIRM_ENTITY_LINES_PER_TYPE) {
      return { ok: false, reason: "firm_entity_type_ceiling" };
    }
    return { ok: true };
  }
  const key = personKeyForEmail(added.email);
  const personRows = rows.filter((r) => !isFirmEntityRow(r) && personKeyForEmail(r.email) === key).sort(byAge);
  const myIdx = personRows.findIndex((r) => r.id === added.id);
  if (myIdx >= PER_PERSON_LINE_CEILING) return { ok: false, reason: "person_line_ceiling" };
  if (myIdx === 0) {
    // This row is the person's earliest line, i.e. they are NEW. Rank people by their earliest line.
    const earliest = new Map<string, SeatRowOrdered>();
    for (const r of [...rows].filter((x) => !isFirmEntityRow(x)).sort(byAge)) {
      const k = personKeyForEmail(r.email);
      if (!earliest.has(k)) earliest.set(k, r);
    }
    const ranked = [...earliest.values()].sort(byAge);
    if (ranked.findIndex((r) => personKeyForEmail(r.email) === key) >= seatCap) return { ok: false, reason: "seat_cap" };
  }
  return { ok: true };
}
