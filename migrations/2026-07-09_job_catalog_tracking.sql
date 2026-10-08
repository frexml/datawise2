-- Adds catalog-push tracking to jobs: set once a job's approved summary has
-- been pushed to the open-source data governance catalog (OpenMetadata).
-- Apply once per environment after deploying the matching backend code.
-- Safe to re-run (IF NOT EXISTS guards).

BEGIN;

ALTER TABLE jobs
    ADD COLUMN IF NOT EXISTS catalog_pushed_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS catalog_url VARCHAR;

COMMIT;
