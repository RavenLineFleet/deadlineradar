-- FRESH-3 (AuditLab, 2026-09-12): cpe_hours.json, reinstatement.json, and
-- renewal_fees.json each have their own 30-day verified_date/last_verified
-- gate in preship_gate.py (check_cpe_hours_currency/check_reinstatement_
-- currency/check_renewal_fee_currency) -- unlike mobility_rules.json's TTL,
-- these three HARD-BLOCK all shipping once a record turns stale. A batch-
-- verification burst (2026-08-13/08-14) put 74 records on a collision
-- course to go stale 2026-09-13/09-14 with zero advance warning -- caught
-- by AuditLab with ~24h notice. The same-day remediation re-stamped those
-- 74 records (plus a routine 39-record cohort from 2026-09-09) with one
-- new date, re-arming an even bigger wall: 157 records stale by
-- 2026-10-13. This is the dedup log for the pre-expiry alert (fires once
-- any record across the three datasets enters its warning window): DAY-
-- keyed (not month-keyed like mobility_staleness_alert_log/0069 or
-- stripe_price_parity_alert_log/0076) -- the warning window here is only
-- 7 days (GATED_DATASET_STALENESS_WARNING_DAYS in scheduler.ts) against a
-- 30-day TTL, a much tighter ratio than mobility's 30-day window on a
-- 180-day TTL, so a monthly nag would go silent for weeks at a time while
-- real risk keeps changing underneath it. Same INSERT-and-report-whether-
-- it-landed shape as stale_data_alert_log (0064), just for this signal.

CREATE TABLE IF NOT EXISTS gated_dataset_staleness_alert_log (
    day TEXT PRIMARY KEY, -- UTC day, ISO 'YYYY-MM-DD'
    sent_at TEXT NOT NULL
);
