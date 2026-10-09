-- Founding Firms (Devin decision 2026-10-09): five CPA firms get Growth-tier
-- access free for 365 days via a Stripe trial with NO card collected, set to
-- CANCEL at day 365 if no card is on file. This table is the ONLY place the
-- "exactly 5, ever" cap lives -- Stripe cannot count it (no promotion code is
-- involved; see AssetLab/state/FOUNDING_FIRMS_DESIGN.md).
--
-- slot PRIMARY KEY + CHECK(slot BETWEEN 1 AND 5): a 6th row is impossible even
-- under concurrent inserts. A slot is consumed at GRANT time, not at checkout,
-- and is never freed by a later firm deletion: firm_id is deliberately NOT a
-- foreign key (a FK would block hardDeleteExpiredFirms(), and a cascade would
-- hand the slot back, breaking "exactly 5 ever"). firm_id is an opaque id; the
-- evidence note is operator-written and must carry no personal data.
--
-- Rows are written ONLY by the operator script (scripts/founding_firm_grant.py).
-- The Worker reads a row at checkout and, at checkout.session.completed,
-- stamps trial_started_at / stripe_subscription_id exactly once.
CREATE TABLE IF NOT EXISTS founding_firm_grants (
    slot INTEGER PRIMARY KEY CHECK (slot BETWEEN 1 AND 5),
    firm_id TEXT NOT NULL UNIQUE,
    granted_at TEXT NOT NULL,        -- ISO-8601 UTC
    verified_by TEXT NOT NULL,       -- 'devin' | 'orchestrator'
    evidence_note TEXT NOT NULL,     -- what was checked; no personal data
    trial_started_at TEXT,           -- set once, when the trial checkout completes
    stripe_subscription_id TEXT      -- the trial subscription, set with trial_started_at
);
