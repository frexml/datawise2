-- Migrate hot JSON columns to JSONB.
-- Apply once per environment after deploying the matching backend code.
--
-- Why JSONB:
--   * binary-packed, indexable
--   * `?`, `->`, `@>` operators work natively
--   * faster to scan/serialize than text-stored JSON
--
-- Safe to re-run (uses USING ... ::jsonb casts and ALTER COLUMN is idempotent
-- once the column type is already jsonb — Postgres no-ops on the second pass).

BEGIN;

ALTER TABLE results
    ALTER COLUMN raw_json         TYPE jsonb USING raw_json::jsonb,
    ALTER COLUMN analysis_summary TYPE jsonb USING analysis_summary::jsonb;

ALTER TABLE stages
    ALTER COLUMN properties TYPE jsonb USING properties::jsonb;

ALTER TABLE links
    ALTER COLUMN properties TYPE jsonb USING properties::jsonb;

ALTER TABLE annotations
    ALTER COLUMN properties TYPE jsonb USING properties::jsonb;

COMMIT;

-- After commit, optional housekeeping (run separately, requires VACUUM):
--   VACUUM (ANALYZE) results, stages, links, annotations;
