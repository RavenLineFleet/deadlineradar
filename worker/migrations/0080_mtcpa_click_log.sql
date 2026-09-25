-- MT eConnect click tracking (Orchestrator directive, 2026-09-25): GET
-- /api/go/mtcpa is a redirect the Montana Society of CPAs eConnect banner
-- and advertorial link point at, so clicks can be counted exactly instead
-- of inferred from traffic noise. One row per click, timestamp only -- no
-- IP, no user-agent, no referrer, nothing that could identify a person.
-- Deliberately per-click (not a single incrementing counter row) so the
-- count survives a Worker restart and can be graphed over time later if
-- useful, at negligible volume (a ~1,400-member newsletter audience).
CREATE TABLE IF NOT EXISTS mtcpa_click_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    clicked_at TEXT NOT NULL  -- ISO-8601 UTC
);
