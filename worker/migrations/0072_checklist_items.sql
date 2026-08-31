-- Roadmap #303 (2026-08-31, roadmap_items table, "Forms & Documents"
-- cluster, orchestrator-directed pull-forward from 1 YEAR OUT): renewal
-- document checklist per license -- Suralink-style upload slots + status
-- per requirement, the passive-calendar-to-active-workflow feature.
--
-- Deliberately firm-customizable rather than a hand-authored per-state
-- requirement catalog: this product's "verified-data brand" posture (see
-- roadmap #315's own gating comment -- a trust counter withheld until the
-- number is real) means shipping a specific per-state document-requirement
-- LIST would be a factual claim ("Texas requires exactly these N
-- documents for renewal") nobody on this team has sourced or verified
-- anywhere. A firm seeds its own checklist per license -- either from a
-- small generic starting template (see store.ts's CHECKLIST_DEFAULT_TEMPLATE,
-- applied client-side/at creation time, never asserted as state-specific)
-- or fully custom items -- and marks each complete, optionally linking the
-- document that satisfies it. A real, sourced, per-state requirement
-- catalog is a natural future enhancement once that data exists, matching
-- migration 0032's own "documents" precedent of shipping the mechanism
-- first, content later.
--
-- Same one-to-many-per-subscriber shape and firm_id-bound-in-every-query
-- convention as documents (migration 0032) / cpe_entries (migration 0009).
-- document_id deliberately has no FOREIGN KEY -- same "must outlive the row
-- it references" reasoning as every cross-reference already in this schema:
-- deleting the linked document must never cascade-delete or break a
-- checklist item, it should just leave the item unlinked (application code
-- re-checks the link is still live via store.getDocumentForFirm, never
-- trusts a stale document_id blindly).
CREATE TABLE IF NOT EXISTS checklist_items (
    id TEXT PRIMARY KEY,
    firm_id TEXT NOT NULL REFERENCES firms(id),
    subscriber_id TEXT NOT NULL REFERENCES subscribers(id),
    label TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',  -- 'pending' | 'complete' | 'not_applicable' -- see store.ts CHECKLIST_ITEM_STATUSES
    document_id TEXT,                        -- optional link into documents.id; no FK, see above
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deleted_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_checklist_items_subscriber ON checklist_items (firm_id, subscriber_id, deleted_at);
