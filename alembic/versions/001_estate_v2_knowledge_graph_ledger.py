"""estate v2 — knowledge graph + ledger + bridge

Revision ID: 001
Revises: 
Create Date: 2026-10-07

Creates 6 new estate tables:
  - estates
  - estate_artifacts
  - ledger_events (with actor_id, actor_email_hash, canonical_payload_hash)
  - migration_plans
  - diff_runs
  - chat_sessions / chat_messages

Legacy jobs/stages/links/reviews tables are left untouched (managed by create_all + hand-written migrations/ still valid).
This revision brings the estate tables under Alembic so future changes are versioned instead of create_all-only.

For SQLite (tests) and Postgres (prod) — JSONB falls back to JSON on SQLite via with_variant.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JsonCol = JSONB().with_variant(sa.JSON(), "sqlite")


def upgrade() -> None:
    # estates
    op.create_table(
        "estates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False, unique=True),
        sa.Column("display_name", sa.String()),
        sa.Column("description", sa.Text()),
        sa.Column("source_type", sa.String(), server_default="synthetic"),
        sa.Column("status", sa.String(), server_default="ACTIVE"),
        sa.Column("ir_version", sa.String(), server_default="0.0.0"),
        sa.Column("ir_generated_at", sa.DateTime(timezone=True)),
        sa.Column("node_count", sa.Integer(), server_default="0"),
        sa.Column("edge_count", sa.Integer(), server_default="0"),
        sa.Column("unresolved_count", sa.Integer(), server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_estates_name", "estates", ["name"], unique=True)

    # estate_artifacts
    op.create_table(
        "estate_artifacts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("estate_id", sa.Integer(), sa.ForeignKey("estates.id"), nullable=False),
        sa.Column("artifact_type", sa.String()),
        sa.Column("fqn", sa.String()),
        sa.Column("file_path", sa.String()),
        sa.Column("raw_hash", sa.String()),
        sa.Column("parsed_json", JsonCol),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_estate_artifacts_estate_id", "estate_artifacts", ["estate_id"])
    op.create_index("ix_estate_artifacts_fqn", "estate_artifacts", ["fqn"])

    # ledger_events — hash-chained, with actor_id/email_hash + canonical hash (P0 fix)
    op.create_table(
        "ledger_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("estate_id", sa.Integer(), sa.ForeignKey("estates.id"), nullable=False),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("actor", sa.String(), server_default="system"),
        sa.Column("actor_id", sa.String(), nullable=True),
        sa.Column("actor_email_hash", sa.String(), nullable=True),
        sa.Column("idp_verified", sa.Boolean(), server_default="false"),
        sa.Column("payload", JsonCol, nullable=False),
        sa.Column("canonical_payload_hash", sa.String(), nullable=True),
        sa.Column("prev_hash", sa.String()),
        sa.Column("event_hash", sa.String()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_ledger_events_estate_id", "ledger_events", ["estate_id"])
    op.create_index("ix_ledger_events_event_type", "ledger_events", ["event_type"])
    op.create_index("ix_ledger_events_event_hash", "ledger_events", ["event_hash"])

    # migration_plans
    op.create_table(
        "migration_plans",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("estate_id", sa.Integer(), sa.ForeignKey("estates.id"), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("target_platform", sa.String(), server_default="snowflake"),
        sa.Column("scope_description", sa.Text()),
        sa.Column("scope_fqns", JsonCol),
        sa.Column("status", sa.String(), server_default="draft"),
        sa.Column("estimated_days", sa.Float()),
        sa.Column("risk_level", sa.String(), server_default="medium"),
        sa.Column("approved_by", sa.String()),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("generated_ddl_path", sa.String()),
        sa.Column("generated_terraform_path", sa.String()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_migration_plans_estate_id", "migration_plans", ["estate_id"])

    # diff_runs
    op.create_table(
        "diff_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("estate_id", sa.Integer(), sa.ForeignKey("estates.id"), nullable=False),
        sa.Column("plan_id", sa.Integer(), sa.ForeignKey("migration_plans.id")),
        sa.Column("status", sa.String(), server_default="pending"),
        sa.Column("tolerances", JsonCol),
        sa.Column("total_rows_compared", sa.Integer()),
        sa.Column("mismatched_rows", sa.Integer(), server_default="0"),
        sa.Column("masked_columns", JsonCol),
        sa.Column("sampling_method", sa.String(), server_default="full"),
        sa.Column("sampling_n", sa.Integer()),
        sa.Column("bound_95", sa.String()),
        sa.Column("per_table_results", JsonCol),
        sa.Column("continuity_passed", sa.Boolean(), server_default="false"),
        sa.Column("continuity_flags", JsonCol),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )

    # chat_sessions / chat_messages
    op.create_table(
        "chat_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("estate_id", sa.Integer(), sa.ForeignKey("estates.id"), nullable=False),
        sa.Column("title", sa.String(), server_default="New conversation"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "chat_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("session_id", sa.Integer(), sa.ForeignKey("chat_sessions.id"), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("citations", JsonCol),
        sa.Column("tool_calls", JsonCol),
        sa.Column("was_refused", sa.Boolean(), server_default="false"),
        sa.Column("latency_ms", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("chat_messages")
    op.drop_table("chat_sessions")
    op.drop_table("diff_runs")
    op.drop_table("migration_plans")
    op.drop_table("ledger_events")
    op.drop_table("estate_artifacts")
    op.drop_table("estates")
