-- AuditLab BILL-17 (MEDIUM, 2026-09-09): the site advertises FIRM_TIERS'
-- priceUsd via generate.py/tiers.ts, but nothing ever reads back what
-- Stripe would actually charge behind each STRIPE_PRICE_FIRM_* Price id --
-- an ordinary dashboard edit or a repointed env var silently desyncs the
-- advertised price from the charged one, with every existing gate still
-- passing (preship_gate.py only ever compares HTML to tiers.ts, never to
-- Stripe). This is the dedup log for the nightly parity check's alert:
-- month-keyed, not day-keyed, since a price mismatch is a slow-moving
-- config drift, not something that needs a daily nag once known -- same
-- INSERT-and-report-whether-it-landed shape as mobility_staleness_alert_log
-- (0069), just for a different slow-moving signal.

CREATE TABLE IF NOT EXISTS stripe_price_parity_alert_log (
    month TEXT PRIMARY KEY, -- UTC month, ISO 'YYYY-MM'
    sent_at TEXT NOT NULL
);
