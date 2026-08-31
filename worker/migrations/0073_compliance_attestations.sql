-- Roadmap #304 + #306 (2026-08-31, "Forms & Documents" cluster): compliance
-- sign-off audit trail, and the lightweight internal e-signature that
-- produces each entry. Built as ONE feature, not two -- #306's
-- "e-signature" IS the act that creates a #304 attestation row; there is
-- no separate signature artifact to store beyond the typed name captured
-- right here. Firms need this as a diligence artifact for peer review /
-- malpractice insurance, distinct from the reminder-DATES audit trail
-- (reminder_log, migration 0035, exported via GET /firm/audit-trail) --
-- this tracks WHO reviewed/attested a license's status, not when a
-- reminder email fired.
--
-- Durable, denormalized, no-FK log -- same "must outlive the rows it
-- describes" convention as activity_log (migration 0025) and reminder_log
-- (migration 0035); see reminder_log's own docstring for why this is a
-- NEW table rather than a column bolted onto subscribers or firm_members.
--
-- SECURITY NOTE (not just documentation -- read this before touching the
-- handler that writes this table): the actual guarantee here is the
-- authenticated, role-gated session that creates the row
-- (requireFirmRole(request, env, "partner", "office_manager") in
-- handleAttestationCreate, index.ts) -- never signature_text.
-- signature_text is a UX/ceremony device matching how a real lightweight
-- internal e-signature reads ("type your name to confirm"), not a
-- cryptographic or legally-binding signature. This is deliberately the
-- "without building a full DocuSign" version the roadmap item asked for;
-- do not let a future change quietly promote signature_text into doing
-- security work it was never designed for.
CREATE TABLE IF NOT EXISTS compliance_attestations (
    id TEXT PRIMARY KEY,
    firm_id TEXT NOT NULL,
    subscriber_id TEXT NOT NULL,
    staff_label TEXT,                  -- snapshot: whose license this attestation is about
    staff_email TEXT,                  -- snapshot: same, same "outlives a removed row" posture as activity_log
    attested_by_member_id TEXT,        -- no FK -- must outlive a later-removed firm_members row
    attested_by_name TEXT NOT NULL,    -- snapshot: the signer's account name at sign time
    attested_by_email TEXT NOT NULL,   -- snapshot: same
    signature_text TEXT NOT NULL,      -- the typed e-signature -- see SECURITY NOTE above
    statement TEXT NOT NULL,           -- the fixed attestation statement signed -- see store.ts ATTESTATION_STATEMENT
    status_snapshot TEXT NOT NULL,     -- JSON: license state_slug/status/deadline fields as of sign time
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_compliance_attestations_subscriber ON compliance_attestations (firm_id, subscriber_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_compliance_attestations_firm ON compliance_attestations (firm_id, created_at ASC);
