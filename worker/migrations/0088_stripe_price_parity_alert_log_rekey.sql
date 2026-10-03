-- BILL-24 (SecurityLab, LOW, confirmed by AuditLab, 2026-10-03): the
-- parity alert deduped on MONTH alone -- first mismatch wins -- so a
-- DIFFERENT, later mismatch in the same month sent nothing (AuditLab's
-- ablation: mismatch A alerts, mismatch B the same month sends 0, the
-- same B with the month log cleared sends fine). Worst case ~29 days of
-- a tier refusing every checkout with no alert.
--
-- Rekeyed on (month, mismatch_signature) -- a stable hash of the CURRENT
-- mismatch set's content -- so a changed mismatch set claims its own row
-- and alerts once, while an unchanged, persistently-failing set still
-- sends at most once per month (the original "slow-moving signal, not a
-- daily nag" intent, preserved). Dropped and recreated rather than
-- ALTERed -- this is a dedup log with no standalone value once
-- superseded (same reasoning as clearing a stale cache), and a PRIMARY
-- KEY composition can't be ALTERed in place.
DROP TABLE IF EXISTS stripe_price_parity_alert_log;

CREATE TABLE stripe_price_parity_alert_log (
    month TEXT NOT NULL,               -- UTC month, ISO 'YYYY-MM'
    mismatch_signature TEXT NOT NULL,  -- stable hash of the current mismatch set's content
    sent_at TEXT NOT NULL,
    PRIMARY KEY (month, mismatch_signature)
);
