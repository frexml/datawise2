-- Adds support for the reject -> edit-or-rerun review workflow.
-- Apply once per environment after deploying the matching backend code.
-- Safe to re-run (IF NOT EXISTS guard).

BEGIN;

ALTER TABLE reviews
    ADD COLUMN IF NOT EXISTS edited_by_reviewer BOOLEAN DEFAULT FALSE;

COMMIT;
