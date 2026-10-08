-- Adds priority flagging to jobs, so portfolio coverage can be tracked
-- against "priority jobs" specifically (the docs' Phase 05 exit-gate
-- language — "≥90% of priority jobs" — a different denominator than "all
-- jobs"). The other portfolio metrics in this phase (inefficiency counts,
-- ScopeIQ rollups, review turnaround, SLA overdue) are pure query
-- additions and need no schema change.
-- Apply once per environment after deploying the matching backend code.
-- Safe to re-run (IF NOT EXISTS guard).

BEGIN;

ALTER TABLE jobs
    ADD COLUMN IF NOT EXISTS priority BOOLEAN DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS ix_jobs_priority ON jobs(priority);

COMMIT;
