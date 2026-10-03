-- PR6-B (Devin, 2026-10-02, via orchestrator): "Banner + email to the admin
-- before day 14 and at expiry." Two write-once-ish markers on firms, same
-- "a timestamp on the row itself" dedup shape as referral_reward_applied_at
-- (migration 0018) rather than a month-keyed global claim table -- these
-- are per-firm, per-recipient notices, not one shared internal alert.
--
-- trial_ending_soon_notified_at: set the first time the "your trial ends
-- in N days" email sends for this firm. Never reset -- one notice per
-- trial, same "one trial per firm" posture trial_ends_at itself has.
-- roster_paused_notified_at: set the first time the "your trial has ended
-- and N staff are paused" email sends. Separate column (not reused) since
-- a firm can legitimately need one notice but not the other (under cap at
-- expiry -- no pausing, no second email).
ALTER TABLE firms ADD COLUMN trial_ending_soon_notified_at TEXT;
ALTER TABLE firms ADD COLUMN roster_paused_notified_at TEXT;
