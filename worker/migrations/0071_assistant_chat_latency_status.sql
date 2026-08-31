-- AuditLab MON-4 (MEDIUM, 2026-08-31): the latency alert's p95/max were
-- computed over EVERY logged request, including the droplet's own ~0.4s
-- 429 fast-reject -- proven to dilute the stat under exactly the load a
-- degradation would also cause (their simulation: 950 429-rows + 50 real
-- 26s responses -> p95 reads 400ms, breach=false). Never rewrite 0070 now
-- that it's already applied to remote (normal migration discipline) --
-- ADD COLUMN instead.
--
-- 'success' | 'rate_limited' | 'error' mirrors AssistantDropletResult's own
-- shape in index.ts (ok / rateLimited / neither). Backfilled 'success' for
-- any pre-existing rows (all of today's real samples so far were 200s;
-- this is the conservative default -- see store.ts's
-- recentAssistantChatLatencyStats for how it's actually used, only
-- 'success' rows feed p95/max).
ALTER TABLE assistant_chat_latency_log ADD COLUMN status TEXT NOT NULL DEFAULT 'success';

CREATE INDEX IF NOT EXISTS idx_assistant_chat_latency_ts_status ON assistant_chat_latency_log (ts, status);
