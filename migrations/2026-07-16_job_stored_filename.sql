-- Adds Job.stored_filename — the UUID-based name actually on disk under
-- uploads/, as opposed to Job.filename (the original upload name, not
-- unique/safe to use as a disk path). Needed to serve the raw source file
-- back to the frontend (Raw vs. Scaffold comparison view) given only a
-- job_id — previously unrecoverable, see the now-removed TODO comments in
-- endpoints.py's delete_job.
-- Apply once per environment after deploying the matching backend code.
-- Safe to re-run (IF NOT EXISTS guard). Existing rows will have NULL —
-- their raw file can't be recovered retroactively since the upload
-- endpoint never persisted the on-disk name before this migration.

BEGIN;

ALTER TABLE jobs
    ADD COLUMN IF NOT EXISTS stored_filename VARCHAR;

COMMIT;
