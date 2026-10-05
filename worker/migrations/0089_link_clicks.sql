-- Tracked short links (Orchestrator directive, 2026-10-05, Devin RED-approved:
-- "We need to be able to track if someone from linkedin and email check us
-- out."). GET /r/<code> redirects through an allowlist (src/tracked_links.ts)
-- and logs exactly one row per hit before redirecting, so an outreach click
-- is countable even though Cloudflare Web Analytics has no query-string
-- dimension (confirmed: requestQuery is an unknown field there).
--
-- No PII: no IP, no raw user-agent string, no email/subscriber id -- just
-- the code, when, a coarse geo/network signal, and the automated-traffic
-- classification tracked_links.ts computed. ua_class/is_human let the
-- report tool (Orchestrator/tools/link_clicks.py) separate real visitors
-- from mail-security-gateway prefetches and link-preview bots without
-- deleting the scanner rows (kept, flagged, per the directive).
CREATE TABLE IF NOT EXISTS link_clicks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL,
    clicked_at TEXT NOT NULL,   -- ISO-8601 UTC
    country TEXT,               -- request.cf.country, e.g. "US" -- null if CF didn't supply it (local/test)
    colo TEXT,                  -- request.cf.colo, e.g. "DEN" -- null if CF didn't supply it
    asn INTEGER,                 -- request.cf.asn -- null if CF didn't supply it
    ua_class TEXT NOT NULL,      -- 'browser' | 'bot' | 'mail_scanner'
    is_human INTEGER NOT NULL    -- 1 if ua_class = 'browser', 0 otherwise -- denormalized for a cheap WHERE
);

-- The click handler's read is a point lookup on `code` (the rolling
-- burst-detection count in tracked_links.ts) -- never a full-table scan on
-- the write path, per the directive. The report tool's per-code rollups
-- use the same index.
CREATE INDEX IF NOT EXISTS idx_link_clicks_code ON link_clicks(code, clicked_at);
