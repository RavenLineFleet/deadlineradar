-- "Log a course once, apply it to the states I pick" (Devin 2026-10-10, roadmap #344).
-- A group is just a shared id on ordinary cpe_entries rows, one row per chosen state
-- line of the SAME person (same firm, same exact email). Every reader of cpe_entries
-- keeps working untouched: progress calc, reports CSV, rollups and soft-delete all see
-- plain rows. NULL for every existing row; no backfill. group_id is display + "remove
-- all linked" only, never an authority: each row's own firm_id/subscriber_id are what
-- every query binds on.
ALTER TABLE cpe_entries ADD COLUMN group_id TEXT;
CREATE INDEX IF NOT EXISTS idx_cpe_entries_firm_group ON cpe_entries (firm_id, group_id);
