-- SEC-96 (SecurityLab, confirmed MEDIUM by AuditLab 2026-11-14): a lease for
-- the email reminder tier claim. subscribers.reminders_sent is one bit per
-- tier, so "claimed, send still in flight" and "sent" were the same value:
-- when a failed send's last-resort unclaim was itself swallowed, the tier
-- stayed in reminders_sent forever and the reminder was silently lost.
--
-- One row per (subscriber, cycle, tier) claim. claimReminderThreshold()
-- inserts it in the same D1 batch as the reminders_sent UPDATE, under the
-- same optimistic predicate, so the two can't disagree. completed_at is set
-- once send() reports success. A row whose completed_at is still NULL long
-- after claimed_at is a stranded claim, and the reminder pass re-leases it.
--
-- No backfill: a tier already in reminders_sent with no row here counts as
-- completed, so this deploy can't trigger a mass resend.
-- Times are epoch milliseconds.
CREATE TABLE reminder_claims (
    subscriber_id TEXT NOT NULL,
    cycle INTEGER NOT NULL,
    threshold_days INTEGER NOT NULL,
    claimed_at INTEGER NOT NULL,
    completed_at INTEGER,
    PRIMARY KEY (subscriber_id, cycle, threshold_days)
);

CREATE INDEX idx_reminder_claims_open ON reminder_claims (completed_at, claimed_at);
