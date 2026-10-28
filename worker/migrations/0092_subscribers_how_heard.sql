-- Self-reported "How did you hear about us?" (Orchestrator directive,
-- 2026-10-28, Devin-approved). Deliberately separate from first_touch_src
-- (0082): that column is inferred from our own tagged links, this one is
-- whatever the visitor volunteers on the signup form. how_heard is one of
-- validation.ts's HOW_HEARD_ALLOWLIST keys; how_heard_detail is optional
-- free text (<=120 chars) kept only for "referral"/"other". Both NULL for
-- every row before this build and whenever the visitor skips the question.
ALTER TABLE subscribers ADD COLUMN how_heard TEXT;
ALTER TABLE subscribers ADD COLUMN how_heard_detail TEXT;
