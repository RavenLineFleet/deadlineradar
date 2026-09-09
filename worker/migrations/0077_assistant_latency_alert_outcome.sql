-- AuditLab MON-6 (MEDIUM, 2026-09-09): a failed daily latency alert used to
-- DELETE its own claim row ("so a later tick the same day could retry") --
-- but this cron is a single DAILY trigger (worker/wrangler.toml), so there
-- is no later tick the same day, and the delete only erased the sole
-- database evidence that an alert was ever attempted. Reproduced live
-- 2026-09-09: a real breach (41.3s sample) was detected and a send was
-- attempted, all 3 retries failed, the row vanished, and two independent
-- agents (plus AssetLab itself, on the first read) misread the ~8-10s
-- window before deletion as proof of a successful send.
--
-- Fix: never delete the row. Persist the outcome instead, so "did the
-- alert get out today" is a query, not a forensics exercise that depends
-- on reading the table inside a narrow race window.
ALTER TABLE assistant_latency_alert_log ADD COLUMN outcome TEXT NOT NULL DEFAULT 'sent';
ALTER TABLE assistant_latency_alert_log ADD COLUMN detail TEXT;
