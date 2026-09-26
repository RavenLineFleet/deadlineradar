/**
 * DeadlineRadar Worker -- email sending (Phase 2).
 *
 * Two responsibilities:
 *   1. sendEmail() -- one transactional send through Resend's v1 mail-send
 *      API. No tracking is enabled (these are transactional, not marketing --
 *      Resend has no per-request tracking toggle; tracking is an
 *      account-level dashboard setting, confirmed off).
 *   2. checkAndCountSend() -- a hard DAILY send cap (circuit breaker) backed by
 *      the send_counters table (migration 0004). Protects the free-tier quota
 *      and, more importantly, sender reputation: a bug or attack that tries to
 *      blow through a burst of sends gets refused once the cap is hit for the
 *      UTC day, instead of getting the whole domain flagged as a spammer.
 *
 * Orchestrator directive (2026-09-23, Devin: "Replace SendGrid completely"):
 * SendGrid's trial ended 09-02 and its account was credit-blocked; Resend was
 * wired as a fallback-swap the same day (3746f9307), cut over to active
 * (b5966d214), live-tested (ff49b26c7/5cb9474b5), then made the sole
 * transport here -- SendGrid's code path, API URL, and every dual-transport
 * branch are gone, not just unused. `sendViaSendGrid()`'s old name and its
 * SendGrid-specific docs are gone with it; see git history (pre-`SendGrid-
 * removal` commits) for the SendGrid-era implementation if it's ever needed.
 *
 * The Resend API key is read from env.RESEND_API_KEY -- a wrangler secret,
 * never hardcoded, never committed. If it is unset, every caller's own
 * `if (!env.RESEND_API_KEY) return` guard means this is never reached.
 */

import type { BuiltEmail } from "./emails";

const RESEND_API_URL = "https://api.resend.com/emails";
const FROM_EMAIL = "noreply@deadline-radar.com";
const FROM_NAME = "Deadline-Radar";
const SEND_TIMEOUT_MS = 10_000;

export const DEFAULT_DAILY_SEND_CAP = 300;

function todayUtc(): string {
  return new Date().toISOString().slice(0, 10); // 'YYYY-MM-DD'
}

/** AuditLab CAP-1 (MEDIUM, 2026-08-10, confirmed for a fix 2026-08-12): all
 * nine per-channel resolvers used `Number.isFinite(n) && n > 0 ? n :
 * DEFAULT_...` -- which silently restores the default cap for a literal `0`,
 * the exact value an operator sets to stop a channel mid-incident. `0` is
 * not "unset"; it is the one deliberate kill switch this codebase has, since
 * no `*_ENABLED`/`DISABLE` flag exists anywhere. Shared here so the fix (and
 * any future channel) can't drift back into the bug: unset/empty/unparseable/
 * negative all fall back to the safe default, but a literal non-negative
 * integer -- including 0 -- is honored exactly as configured. */
export function resolveDailySendCap(envValue: string | undefined, defaultCap: number): number {
  if (envValue === undefined || envValue === "") return defaultCap;
  const n = Number.parseInt(envValue, 10);
  return Number.isFinite(n) && n >= 0 ? n : defaultCap;
}

/**
 * Atomic daily circuit breaker. Increments today's counter and returns true
 * only if the send is still under the cap. The whole check-increment is a
 * single D1 UPSERT that (a) inserts the day at count=1 or (b) increments it
 * ONLY while it is still below the cap; the statement then reports whether a
 * row was written. Because it is one statement, it cannot race the way the
 * Python CircuitBreakerSender needed an explicit process-wide lock to prevent.
 *
 * Returns true = under cap, send may proceed. false = cap reached, refuse.
 */
export async function checkAndCountSend(db: D1Database, cap: number): Promise<boolean> {
  // AuditLab CAP-1: the INSERT arm below always writes count=1 on the day's
  // first call regardless of cap -- WHERE only guards the UPDATE arm -- so
  // cap=0 would still let exactly one send through per day without this
  // guard. A real halt must not depend on which call happens to run first.
  if (cap <= 0) return false;
  const day = todayUtc();
  // INSERT the day at 1, or (on conflict) bump the count only if still under
  // the cap. The conditional WHERE on the UPDATE arm means once count == cap,
  // no further row is written and meta.changes is 0.
  const result = await db
    .prepare(
      `INSERT INTO send_counters (day, count) VALUES (?1, 1)
       ON CONFLICT(day) DO UPDATE SET count = count + 1 WHERE count < ?2`
    )
    .bind(day, cap)
    .run();
  return (result.meta.changes ?? 0) > 0;
}

/** AuditLab TS-1 (2026-08-05): action/signup emails (confirmation, firm-lead,
 * firm-signup, firm-login, subscriber-login) used to spend from the SAME
 * counter the reminder scheduler does, so a spam wave against any of the
 * ad-blocker-relaxed routes could exhaust the shared daily cap and silently
 * stop real deadline reminders for the rest of the UTC day. This is the
 * IDENTICAL circuit breaker against migration 0019's separate
 * `action_send_counters` table -- every caller sending an action/transactional
 * email (never a reminder) must use this instead of checkAndCountSend(). */
export const DEFAULT_DAILY_ACTION_SEND_CAP = 300;

export async function checkAndCountActionSend(db: D1Database, cap: number): Promise<boolean> {
  if (cap <= 0) return false; // AuditLab CAP-1 -- see checkAndCountSend()'s own comment
  const day = todayUtc();
  const result = await db
    .prepare(
      `INSERT INTO action_send_counters (day, count) VALUES (?1, 1)
       ON CONFLICT(day) DO UPDATE SET count = count + 1 WHERE count < ?2`
    )
    .bind(day, cap)
    .run();
  return (result.meta.changes ?? 0) > 0;
}

/** Roadmap #34 (2026-08-08): a THIRD, fully independent daily circuit
 * breaker for the drip course -- same identical shape as
 * checkAndCountSend()/checkAndCountActionSend() above, against its own
 * `drip_course_send_counters` table (migration 0049), so this marketing
 * sequence can never compete with real deadline reminders or transactional
 * sends for budget, even indirectly. */
export const DEFAULT_DAILY_DRIP_COURSE_SEND_CAP = 100;

export async function checkAndCountDripCourseSend(db: D1Database, cap: number): Promise<boolean> {
  if (cap <= 0) return false; // AuditLab CAP-1 -- see checkAndCountSend()'s own comment
  const day = todayUtc();
  const result = await db
    .prepare(
      `INSERT INTO drip_course_send_counters (day, count) VALUES (?1, 1)
       ON CONFLICT(day) DO UPDATE SET count = count + 1 WHERE count < ?2`
    )
    .bind(day, cap)
    .run();
  return (result.meta.changes ?? 0) > 0;
}

/** Roadmap #9/#319 (2026-08-08): a FOURTH independent daily circuit
 * breaker, same identical shape as the three above, against its own
 * `rule_change_alert_send_counters` table (migration 0050) -- this is
 * cron-triggered (not user-request-triggered), same isolation reasoning
 * the reminder and drip-course passes already established for themselves. */
export const DEFAULT_DAILY_RULE_CHANGE_ALERT_SEND_CAP = 100;

export async function checkAndCountRuleChangeAlertSend(db: D1Database, cap: number): Promise<boolean> {
  if (cap <= 0) return false; // AuditLab CAP-1 -- see checkAndCountSend()'s own comment
  const day = todayUtc();
  const result = await db
    .prepare(
      `INSERT INTO rule_change_alert_send_counters (day, count) VALUES (?1, 1)
       ON CONFLICT(day) DO UPDATE SET count = count + 1 WHERE count < ?2`
    )
    .bind(day, cap)
    .run();
  return (result.meta.changes ?? 0) > 0;
}

/** Roadmap #24 (2026-08-08): a FIFTH independent daily circuit breaker,
 * same identical shape as the four above, against its own
 * `digest_send_counters` table (migration 0051) -- same cron-vs-request-
 * triggered isolation reasoning as the other cron passes. */
export const DEFAULT_DAILY_DIGEST_SEND_CAP = 100;

export async function checkAndCountDigestSend(db: D1Database, cap: number): Promise<boolean> {
  if (cap <= 0) return false; // AuditLab CAP-1 -- see checkAndCountSend()'s own comment
  const day = todayUtc();
  const result = await db
    .prepare(
      `INSERT INTO digest_send_counters (day, count) VALUES (?1, 1)
       ON CONFLICT(day) DO UPDATE SET count = count + 1 WHERE count < ?2`
    )
    .bind(day, cap)
    .run();
  return (result.meta.changes ?? 0) > 0;
}

/** Roadmap #20 (2026-08-08): a SIXTH independent daily circuit breaker,
 * same identical shape as the five above, against its own
 * `slack_alert_send_counters` table (migration 0052) -- same cron-vs-
 * request-triggered isolation reasoning as the other cron passes. */
export const DEFAULT_DAILY_SLACK_ALERT_SEND_CAP = 100;

export async function checkAndCountSlackAlertSend(db: D1Database, cap: number): Promise<boolean> {
  if (cap <= 0) return false; // AuditLab CAP-1 -- see checkAndCountSend()'s own comment
  const day = todayUtc();
  const result = await db
    .prepare(
      `INSERT INTO slack_alert_send_counters (day, count) VALUES (?1, 1)
       ON CONFLICT(day) DO UPDATE SET count = count + 1 WHERE count < ?2`
    )
    .bind(day, cap)
    .run();
  return (result.meta.changes ?? 0) > 0;
}

/** Roadmap #21 (2026-08-08): a SEVENTH independent daily circuit breaker,
 * same identical shape as the six above, against its own
 * `teams_alert_send_counters` table (migration 0053) -- same cron-vs-
 * request-triggered isolation reasoning as the other cron passes. */
export const DEFAULT_DAILY_TEAMS_ALERT_SEND_CAP = 100;

export async function checkAndCountTeamsAlertSend(db: D1Database, cap: number): Promise<boolean> {
  if (cap <= 0) return false; // AuditLab CAP-1 -- see checkAndCountSend()'s own comment
  const day = todayUtc();
  const result = await db
    .prepare(
      `INSERT INTO teams_alert_send_counters (day, count) VALUES (?1, 1)
       ON CONFLICT(day) DO UPDATE SET count = count + 1 WHERE count < ?2`
    )
    .bind(day, cap)
    .run();
  return (result.meta.changes ?? 0) > 0;
}

/** Roadmap #22 (2026-08-09): an EIGHTH independent daily circuit breaker,
 * same identical shape as the seven above, against its own
 * `sms_send_counters` table (migration 0054) -- kept especially tight by
 * default given SMS has a real per-message cost, unlike every other
 * channel. */
export const DEFAULT_DAILY_SMS_SEND_CAP = 50;

export async function checkAndCountSmsSend(db: D1Database, cap: number): Promise<boolean> {
  if (cap <= 0) return false; // AuditLab CAP-1 -- see checkAndCountSend()'s own comment
  const day = todayUtc();
  const result = await db
    .prepare(
      `INSERT INTO sms_send_counters (day, count) VALUES (?1, 1)
       ON CONFLICT(day) DO UPDATE SET count = count + 1 WHERE count < ?2`
    )
    .bind(day, cap)
    .run();
  return (result.meta.changes ?? 0) > 0;
}

/** Roadmap #151 Phase 5 (2026-08-10): a NINTH independent daily circuit
 * breaker, same identical shape as the eight above, against its own
 * `admin_digest_send_counters` table (migration 0061) -- same cron-vs-
 * request-triggered isolation reasoning as the other cron passes. */
export const DEFAULT_DAILY_ADMIN_DIGEST_SEND_CAP = 100;

export async function checkAndCountAdminDigestSend(db: D1Database, cap: number): Promise<boolean> {
  if (cap <= 0) return false; // AuditLab CAP-1 -- see checkAndCountSend()'s own comment
  const day = todayUtc();
  const result = await db
    .prepare(
      `INSERT INTO admin_digest_send_counters (day, count) VALUES (?1, 1)
       ON CONFLICT(day) DO UPDATE SET count = count + 1 WHERE count < ?2`
    )
    .bind(day, cap)
    .run();
  return (result.meta.changes ?? 0) > 0;
}

/** Roadmap #124 (2026-08-13): compliance-news newsletter's own independent
 * daily circuit breaker, same identical shape as every cap above -- against
 * its own `newsletter_send_counters` table (migration 0066), so a bug in
 * this brand-new send path can never compete with reminder/action/digest
 * budget, even indirectly. Confirmation-email sends AND the monthly digest
 * fan-out both spend from this same counter -- they're the same list's
 * mail, not two independent budgets. */
export const DEFAULT_DAILY_NEWSLETTER_SEND_CAP = 300;

export async function checkAndCountNewsletterSend(db: D1Database, cap: number): Promise<boolean> {
  if (cap <= 0) return false; // AuditLab CAP-1 -- see checkAndCountSend()'s own comment
  const day = todayUtc();
  const result = await db
    .prepare(
      `INSERT INTO newsletter_send_counters (day, count) VALUES (?1, 1)
       ON CONFLICT(day) DO UPDATE SET count = count + 1 WHERE count < ?2`
    )
    .bind(day, cap)
    .run();
  return (result.meta.changes ?? 0) > 0;
}

/**
 * Case-insensitive, trimmed membership check against a comma-separated
 * allowlist string (env.EMAIL_ALLOWLIST). Returns null when `raw` is
 * unset/empty -- meaning "no allowlist configured, gate is off" -- as
 * distinct from an empty array, so callers can tell "not configured" apart
 * from "configured but empty" if that distinction ever matters.
 */
function parseAllowlist(raw: string | undefined): string[] | null {
  if (!raw) return null;
  const parsed = raw
    .split(",")
    .map((s) => s.trim().toLowerCase())
    .filter((s) => s.length > 0);
  return parsed.length > 0 ? parsed : null;
}

/**
 * Exported for callers OTHER than sendEmail that need the same
 * "is this a known preview/staging test address" membership check (2026-07-30
 * addition: the firm-signup trial gate exempts EMAIL_ALLOWLIST addresses so a
 * tester can still sign up a preview firm with their own real personal
 * address -- EMAIL_ALLOWLIST is never set in production, so this exemption
 * is structurally a no-op there, same as every other EMAIL_ALLOWLIST-gated
 * behavior in this file).
 */
export function isEmailAllowlisted(raw: string | undefined, email: string): boolean {
  const allowlist = parseAllowlist(raw);
  if (!allowlist) return false;
  return allowlist.includes(email.trim().toLowerCase());
}

/**
 * One transactional send via Resend's v1 mail-send API
 * (https://resend.com/docs/api-reference/emails/send-email). Returns true on
 * 2xx, false otherwise (a failed send must never throw up into /subscribe --
 * a subscriber's record is already stored; a transient email failure should
 * not 500 their request). No per-request tracking toggle exists on Resend
 * (unlike SendGrid's old tracking_settings) -- tracking is an account-level
 * dashboard setting, confirmed off.
 *
 * `emailAllowlist` is the raw env.EMAIL_ALLOWLIST value (see env.ts) -- a
 * PREVIEW/STAGING-ONLY safety gate. When it parses to a non-empty list and
 * `toEmail` (trimmed, case-insensitive) is not on it, this function returns
 * false immediately and never calls fetch() -- the recipient is never
 * contacted. When `emailAllowlist` is undefined/empty (the production
 * default), this check is skipped entirely and behavior is byte-identical to
 * before this gate existed.
 *
 * `previewLogBody` is the raw env.EMAIL_PREVIEW_LOG_BODY value (see env.ts) --
 * a SEPARATE preview/staging-only switch, deliberately independent of
 * `emailAllowlist`. AuditLab LOG-1 (LOW, 2026-09-12): this used to be the same
 * condition as the allowlist gate above, which meant setting an allowlist in
 * production (a plausible, well-intentioned "restrict recipients" action)
 * would have silently also enabled full-body credential logging. The two are
 * unrelated capabilities and now require two separate opt-ins.
 */
export async function sendEmail(
  apiKey: string,
  toEmail: string,
  email: BuiltEmail,
  emailAllowlist?: string,
  previewLogBody?: string,
  // Roadmap #19 (2026-08-07): lightweight white-label. Deliberately does NOT
  // change `from` -- every send still originates from FROM_EMAIL/FROM_NAME
  // above, so the domain's own authentication (SPF/DKIM) is untouched and
  // DeadlineRadar remains the sender of record for CAN-SPAM purposes. Only
  // where a REPLY goes changes.
  replyTo?: string
): Promise<boolean> {
  const allowlist = parseAllowlist(emailAllowlist);
  // Preview/staging visibility (2026-07-28; decoupled from the allowlist
  // gate by AuditLab LOG-1, 2026-09-12): log the full built email -- readable
  // live via `wrangler tail --config wrangler.preview.toml`. This is what
  // makes the preview usable even before/without a real RESEND_API_KEY: a
  // tester can grab a magic-link URL (or a reminder email's renew-and-rearm
  // link) straight out of the log stream. Only fires when previewLogBody is
  // explicitly set, which is never true in production -- this line does not
  // exist there.
  if (previewLogBody) {
    console.log(`[preview-email] to=${toEmail} subject=${JSON.stringify(email.subject)}\n${email.textBody}`);
  }
  if (allowlist && !allowlist.includes(toEmail.trim().toLowerCase())) {
    // AuditLab SILENT-5 (LOW, 2026-09-25): MON-5's "never swallow the
    // failure reason" discipline (see the Resend-rejection branch below)
    // was applied there and not here, in the same function -- and this
    // branch's failure mode is the worse of the two: allowlist has no
    // business being set in production (env.ts's own doc comment), but if
    // it ever were, this line is what silently drops every email to every
    // customer, one `return false` at a time, with nothing to grep.
    //
    // SecurityLab (2026-09-25, same day): masking the local part rather
    // than logging toEmail in full -- an earlier version didn't, which
    // re-crossed the exact line AuditLab's LOG-1 drew (full-body/recipient
    // logging must require its OWN var, EMAIL_PREVIEW_LOG_BODY, not ride
    // along on EMAIL_ALLOWLIST's mere presence). This keeps the diagnostic
    // value ("a drop happened, to this domain") without logging a real
    // recipient address on one var alone.
    console.log(`[email-allowlist-drop] to=${toEmail.replace(/^[^@]+/, "***")}`);
    return false;
  }
  const payload: Record<string, unknown> = {
    from: `${FROM_NAME} <${FROM_EMAIL}>`,
    to: [toEmail],
    subject: email.subject,
    text: email.textBody,
    html: email.htmlBody,
    ...(replyTo ? { reply_to: replyTo } : {}),
    ...(email.headers && Object.keys(email.headers).length > 0 ? { headers: email.headers } : {}),
  };

  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), SEND_TIMEOUT_MS);
  try {
    const resp = await fetch(RESEND_API_URL, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${apiKey}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
      signal: controller.signal,
    });
    if (resp.status >= 200 && resp.status < 300) return true;
    // MON-5 (2026-09-02) discipline: log status + a bounded body snippet,
    // never swallow the failure reason -- a non-2xx used to `return false`
    // with the response body never read, so a rejection vanished with zero
    // trace. Resend error bodies carry validation/quota messages, never the
    // API key (Authorization header only, never echoed back).
    let bodySnippet: string;
    try {
      bodySnippet = (await resp.text()).slice(0, 500);
    } catch {
      bodySnippet = "<body unreadable>";
    }
    console.log(`[resend-fail] status=${resp.status} body=${JSON.stringify(bodySnippet)}`);
    return false;
  } catch (err) {
    console.log(`[resend-error] ${String(err)}`);
    return false;
  } finally {
    clearTimeout(timeoutId);
  }
}
