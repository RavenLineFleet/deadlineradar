-- AuditLab (2026-08-31, Devin get-to-100% directive: add latency monitoring).
-- Confirmed recurring: the assistant chat endpoint sits in a ~15-18s band
-- for a normal question (two independent samples, different days) -- LLM-
-- inherent, not itself a bug, but nothing currently makes a real
-- degradation (a stall, a creeping p95) visible in production between
-- AuditLab's manual sweeps. This is the persistent half AuditLab's charter
-- can't build (they're read-only).
--
-- One row per real assistant/chat request (not per attempt -- see
-- callAssistantDroplet's own comment on what "elapsed" measures), logged
-- fire-and-forget so a logging failure never affects the actual response.
-- Self-trims the same way rate_limit_hits (0002) does: old rows are
-- deleted opportunistically by the daily latency-alert pass, not by a
-- separate cleanup job.
CREATE TABLE IF NOT EXISTS assistant_chat_latency_log (
    ts INTEGER NOT NULL, -- unix seconds
    elapsed_ms INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_assistant_chat_latency_ts ON assistant_chat_latency_log (ts);

-- Day-keyed dedup for the alert itself, same INSERT-and-report-whether-it-
-- landed shape as stale_data_alert_log (0064) -- a real degradation is
-- worth at most one email per UTC day, not one per cron tick that still
-- sees it.
CREATE TABLE IF NOT EXISTS assistant_latency_alert_log (
    day TEXT PRIMARY KEY, -- UTC day, ISO 'YYYY-MM-DD'
    sent_at TEXT NOT NULL
);
