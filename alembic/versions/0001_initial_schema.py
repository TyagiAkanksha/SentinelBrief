"""initial schema

PRD §5: the four tables (`alerts`, `verdicts`, `tool_calls`, `eval_runs`) and the index set named
there. Table/column shapes are copied verbatim from the PRD §5 SQL block; hand-reviewed from
`alembic revision --autogenerate` output against `core.models.Base.metadata` (CONVENTIONS.md §6)
run in a scratch schema, then discarded — Alembic's own `alembic_version` bookkeeping table is
never created/dropped by this migration; Alembic manages it itself.

Revision ID: 0001
Revises:
Create Date: 2026-09-07
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | None = None
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """Create the four PRD §5 tables and the PRD §5 index set."""
    op.create_table(
        "alerts",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("fingerprint", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("event_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("raw", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.Text(), server_default="pending", nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_alerts_fingerprint"), "alerts", ["fingerprint"], unique=True)
    op.create_index(
        "ix_alerts_received_at", "alerts", [sa.literal_column("received_at DESC")], unique=False
    )
    op.create_index(
        "ix_alerts_src_ip", "alerts", [sa.literal_column("(raw ->> 'src_ip')")], unique=False
    )

    op.create_table(
        "eval_runs",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("git_sha", sa.Text(), nullable=True),
        sa.Column("prompt_version", sa.Text(), nullable=True),
        sa.Column("model_config", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metrics", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "verdicts",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("alert_id", sa.UUID(), nullable=False),
        sa.Column("severity", sa.Integer(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("confidence", sa.REAL(), nullable=False),
        sa.Column("reasoning", sa.Text(), nullable=False),
        sa.Column("recommended_action", sa.Text(), nullable=False),
        sa.Column("escalate", sa.Boolean(), nullable=False),
        sa.Column("model_primary", sa.Text(), nullable=False),
        sa.Column("model_final", sa.Text(), nullable=False),
        sa.Column("escalated_model", sa.Boolean(), nullable=False),
        sa.Column("prompt_version", sa.Text(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Numeric(precision=10, scale=6), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("severity BETWEEN 1 AND 5", name="ck_verdicts_severity"),
        sa.ForeignKeyConstraint(["alert_id"], ["alerts.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_verdicts_alert_id_created_at",
        "verdicts",
        ["alert_id", sa.literal_column("created_at DESC")],
        unique=False,
    )

    op.create_table(
        "tool_calls",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("verdict_id", sa.UUID(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("tool_name", sa.Text(), nullable=False),
        sa.Column("arguments", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["verdict_id"], ["verdicts.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_tool_calls_verdict_id_seq", "tool_calls", ["verdict_id", "seq"], unique=False
    )


def downgrade() -> None:
    """Drop the four PRD §5 tables in FK-safe (reverse) order."""
    op.drop_index("ix_tool_calls_verdict_id_seq", table_name="tool_calls")
    op.drop_table("tool_calls")

    op.drop_index("ix_verdicts_alert_id_created_at", table_name="verdicts")
    op.drop_table("verdicts")

    op.drop_table("eval_runs")

    op.drop_index("ix_alerts_src_ip", table_name="alerts")
    op.drop_index("ix_alerts_received_at", table_name="alerts")
    op.drop_index(op.f("ix_alerts_fingerprint"), table_name="alerts")
    op.drop_table("alerts")
