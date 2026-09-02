-- MON-5 (2026-09-02, AuditLab MEDIUM, Devin-approved): cron-liveness heartbeat.
--
-- Root cause it closes: on 2026-09-02 the daily assistant-latency alert's
-- SendGrid send got a 4xx; runAssistantLatencyAlertPass unclaimed its own
-- day row on failure and logged nothing, so the failure left ZERO durable
-- trace. Worse, there was no record the nightly scheduled() tick had fired
-- at all -- every sibling pass's side effect is conditional (a reminder only
-- writes on a due reminder, a reseed only below floor, etc.), so on a quiet
-- day "did the cron even run" was unanswerable from D1. That ambiguity is
-- exactly what made the instance hard to diagnose.
--
-- This is the unconditional heartbeat: scheduled() upserts row id=1 at the
-- very top of every tick, BEFORE any gate or conditional pass, so
-- last_run_at is a hard fact. Single-row (CHECK id=1) so it never grows;
-- run_count is a cheap monotonic sanity counter. Deliberately NOT a
-- per-tick log table -- one row is all "is the cron alive and when did it
-- last fire" needs, and a bounded table can't drift into unbounded growth.
CREATE TABLE IF NOT EXISTS scheduler_heartbeat (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    last_run_at TEXT NOT NULL,       -- ISO-8601 UTC of the most recent scheduled() tick
    run_count INTEGER NOT NULL DEFAULT 0
);
