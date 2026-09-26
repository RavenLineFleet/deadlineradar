-- Per-IP rate limiting storage -- see ../src/validation.ts's checkRateLimit()
-- for the query shape this table serves. New migration file rather than editing
-- 0001_init_schema.sql, per normal migration discipline (never rewrite an
-- already-numbered migration once later ones may depend on running after
-- it) -- also true here even though neither migration has been applied to
-- any real D1 instance yet.
--
-- One row per (ip, bucket) rate-limit "hit" (a timestamp of a request that
-- counted against the window). Rows older than the relevant window are
-- deleted opportunistically by validation.ts's checkRateLimit() on every
-- check for that SAME ip+bucket -- so a key that never recurs is never
-- revisited, and its rows are never reclaimed. AuditLab RL-9 (2026-09-26,
-- originated with SecurityLab): this table does NOT self-trim on its own;
-- purgeStaleRateLimitHits() (store.ts), wired into the daily cron, is the
-- actual unkeyed cleanup that bounds its growth.

CREATE TABLE IF NOT EXISTS rate_limit_hits (
    ip TEXT NOT NULL,
    bucket TEXT NOT NULL,
    ts INTEGER NOT NULL -- unix seconds
);

CREATE INDEX IF NOT EXISTS idx_rate_limit_ip_bucket_ts ON rate_limit_hits (ip, bucket, ts);
