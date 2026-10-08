-- Adds granular pipeline-progress tracking to jobs, used to animate the
-- run-in-progress view on the Home page.
-- Apply once per environment after deploying the matching backend code.
-- Safe to re-run (IF NOT EXISTS guard).

BEGIN;

ALTER TABLE jobs
    ADD COLUMN IF NOT EXISTS current_stage VARCHAR;

COMMIT;
