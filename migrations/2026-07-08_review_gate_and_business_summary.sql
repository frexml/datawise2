-- Adds the human review/approval gate table and the business-summary column.
-- Apply once per environment after deploying the matching backend code.
-- Safe to re-run (IF NOT EXISTS guards).

BEGIN;

ALTER TABLE results
    ADD COLUMN IF NOT EXISTS business_summary TEXT;

CREATE TABLE IF NOT EXISTS reviews (
    id           SERIAL PRIMARY KEY,
    job_id       INTEGER REFERENCES jobs(id) ON DELETE CASCADE,
    target_type  VARCHAR,
    target_id    INTEGER,
    status       VARCHAR DEFAULT 'pending_review',
    reviewer     VARCHAR,
    feedback     TEXT,
    reviewed_at  TIMESTAMPTZ,
    created_at   TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_reviews_job_id ON reviews (job_id);

COMMIT;
