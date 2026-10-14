-- Adds Estate.survey_preview — a coarse, deliberately-rounded peek shown at
-- the pending_approval gate (detected systems + a bucketed object-count
-- estimate, e.g. "125+") so an approver isn't clicking "Approve & Run"
-- blind. Kept separate from Estate.survey_todo's per-stage exact
-- counts/details, which still only reveal as each stage completes during
-- the staged discovery run — this column is never the precise final count.
-- Apply once per environment after deploying the matching backend code.
-- Safe to re-run (IF NOT EXISTS guard). Existing rows default to '{}'.

BEGIN;

ALTER TABLE estates
    ADD COLUMN IF NOT EXISTS survey_preview JSONB DEFAULT '{}'::jsonb;

COMMIT;
