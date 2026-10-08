-- Adds domain/wave tagging to jobs, for portfolio-scale engagements that
-- group jobs by business domain (e.g. "Fees") and migration wave (e.g.
-- "Wave 1") rather than tracking coverage as one flat portfolio number.
-- Apply once per environment after deploying the matching backend code.
-- Safe to re-run (IF NOT EXISTS guards).

BEGIN;

ALTER TABLE jobs
    ADD COLUMN IF NOT EXISTS domain VARCHAR,
    ADD COLUMN IF NOT EXISTS wave VARCHAR;

CREATE INDEX IF NOT EXISTS ix_jobs_domain ON jobs(domain);
CREATE INDEX IF NOT EXISTS ix_jobs_wave ON jobs(wave);

COMMIT;
