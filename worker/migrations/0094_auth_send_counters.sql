-- SEC-109-R1/R2 (SecurityLab) + AuditLab detection-notice class, 2026-11-19:
-- per-class daily admission counters for authCritical action sends. The
-- reserved top third of action_send_counters was drainable through its own
-- sign-in routes, so each authCritical send now first takes a unit from its
-- class's row here (firm_session / firm_link / subscriber, see
-- sender.ts AUTH_SEND_CLASS_TENTHS). Keyed on day + class only, never IP or
-- account. Same circuit-breaker shape as action_send_counters.
CREATE TABLE IF NOT EXISTS auth_send_counters (
    day TEXT NOT NULL,   -- UTC date, ISO 'YYYY-MM-DD'
    class TEXT NOT NULL, -- firm_session | firm_link | subscriber
    count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, class)
);
