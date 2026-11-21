-- BILL-70 (AuditLab 2026-11-20, MEDIUM): the once-per-UTC-day evidence rows
-- (stripe_sig_fail / stripe_mode_reject / stripe_drop / stripe_handler_throw)
-- were INSERT ... ON CONFLICT DO NOTHING, so a day held one bit: our own
-- control POST, a passing scanner and a real Stripe delivery to a
-- wrong-secret endpoint all collapsed into the same single row. These two
-- columns turn each evidence row into a counter: hit_count is incremented on
-- every hit, last_seen_at moves to the latest one (received_at stays the
-- first). Both are NULL on every other row in the table (ledger events,
-- email-claim rows), and a NULL hit_count on an evidence row means it was
-- written by pre-0095 code, i.e. it counts as 1.
ALTER TABLE stripe_webhook_events ADD COLUMN hit_count INTEGER;
ALTER TABLE stripe_webhook_events ADD COLUMN last_seen_at TEXT;
