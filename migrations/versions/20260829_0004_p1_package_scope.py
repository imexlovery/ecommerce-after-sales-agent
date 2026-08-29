"""Bind P1 investigation cases and tickets to an optional target shipment.

Revision ID: 20260829_0004
Revises: 20260828_0003
Create Date: 2026-08-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260829_0004"
down_revision: str | Sequence[str] | None = "20260828_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "investigation_cases",
        sa.Column("target_shipment_id", sa.String(length=64), nullable=True),
    )
    op.drop_index("uq_tickets_one_active_per_order_issue", table_name="tickets")
    op.add_column(
        "tickets",
        sa.Column("target_shipment_id", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_tickets_target_shipment_id",
        "tickets",
        ["target_shipment_id"],
    )
    op.create_index(
        "uq_tickets_one_active_per_order_issue",
        "tickets",
        ["authorized_order_id", "issue_type"],
        unique=True,
        sqlite_where=sa.text(
            "ticket_state = 'active' AND target_shipment_id IS NULL"
        ),
    )
    op.create_index(
        "uq_tickets_one_active_per_order_issue_target",
        "tickets",
        ["authorized_order_id", "issue_type", "target_shipment_id"],
        unique=True,
        sqlite_where=sa.text(
            "ticket_state = 'active' AND target_shipment_id IS NOT NULL"
        ),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_tickets_one_active_per_order_issue_target", table_name="tickets"
    )
    op.drop_index("uq_tickets_one_active_per_order_issue", table_name="tickets")
    op.drop_index("ix_tickets_target_shipment_id", table_name="tickets")
    op.drop_column("tickets", "target_shipment_id")
    op.drop_column("investigation_cases", "target_shipment_id")
