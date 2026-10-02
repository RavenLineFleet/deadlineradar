-- Privacy-safe source attribution (Orchestrator directive, 2026-10-02): a
-- daily per-channel counter, not a per-event log. No IP, no user-agent, no
-- referrer, no cookie -- just a date and an allow-listed channel tag
-- (worker/src/validation.ts's ATTRIBUTION_SRC_ALLOWLIST), incremented once
-- per page-load-with-?src=. Bounded growth by design: at most
-- (days-tracked x allow-list-size) rows ever, never one row per visitor.
CREATE TABLE IF NOT EXISTS attribution_daily (
    date TEXT NOT NULL,              -- ISO-8601 UTC date, e.g. 2026-10-02
    src TEXT NOT NULL,               -- allow-listed channel tag
    hit_count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (date, src)
);
