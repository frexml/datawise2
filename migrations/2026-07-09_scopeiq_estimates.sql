-- Adds the scopeiq_estimates table: one per-job delivery-effort estimate
-- produced by the ScopeIQ agent (decompose -> per-dimension research ->
-- aggregate), superseding the earlier estate-wide JSON export.
-- Apply once per environment after deploying the matching backend code.
-- Safe to re-run (IF NOT EXISTS guards).

BEGIN;

CREATE TABLE IF NOT EXISTS scopeiq_estimates (
    id SERIAL PRIMARY KEY,
    job_id INTEGER UNIQUE REFERENCES jobs(id) ON DELETE CASCADE,
    status VARCHAR DEFAULT 'pending',
    package_id VARCHAR,
    dimensions JSONB,
    role_day_totals JSONB,
    uplift_adjustments JSONB,
    risk_adjustments JSONB,
    total_days_base FLOAT,
    total_days_adjusted FLOAT,
    complexity_tier VARCHAR,
    error TEXT,
    generated_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_scopeiq_estimates_job_id ON scopeiq_estimates(job_id);

COMMIT;
