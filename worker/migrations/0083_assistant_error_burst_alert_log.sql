-- MON-9 (AuditLab, MEDIUM, 2026-10-02): the existing assistant_latency_
-- alert_log (0070) alerts on p95/max computed from status='success' rows
-- only -- a total outage where every leg fails FAST (the droplet's own
-- apology, or a thrown exception) actually LOWERS p95, making that alert
-- strictly less likely to fire during the exact failure it exists to
-- catch (confirmed live: the 2026-10-02 chat outage logged entirely as
-- 'success' at ~1.5-3s). This is the complementary, count-based signal:
-- how many of the most recent N chats actually errored, independent of
-- timing. Own day-keyed dedup table rather than reusing 0070's -- a
-- genuinely different trigger condition sharing one row would make
-- "which alert fired today" ambiguous from the table alone. Same
-- INSERT-and-report-whether-it-landed shape as every prior alert log
-- (stale_data_alert_log/0064, gated_dataset_staleness_alert_log/0078).

CREATE TABLE IF NOT EXISTS assistant_error_burst_alert_log (
    day TEXT PRIMARY KEY, -- UTC day, ISO 'YYYY-MM-DD'
    sent_at TEXT NOT NULL,
    outcome TEXT NOT NULL DEFAULT 'sent',
    detail TEXT
);
