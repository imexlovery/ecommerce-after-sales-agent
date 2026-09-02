"""Allow informational business-reply intents in persisted triage records.

Revision ID: 20260830_0005
Revises: 20260829_0004
Create Date: 2026-08-30
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260830_0005"
down_revision: str | Sequence[str] | None = "20260829_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_EXPANDED_INTENTS = (
    "intent IN ('signed_not_received', 'stalled_tracking', 'capability_help', "
    "'order_id_help', 'tracking_status_query', 'delivery_eta_info', "
    "'change_delivery_info', 'refund_return_info', 'human_support_request', "
    "'thanks_close', 'other_logistics', 'ambiguous', 'out_of_scope', 'prohibited')"
)
_ORIGINAL_INTENTS = (
    "intent IN ('signed_not_received', 'stalled_tracking', 'other_logistics', "
    "'ambiguous', 'out_of_scope', 'prohibited')"
)


def upgrade() -> None:
    with op.batch_alter_table("triage_records") as batch_op:
        batch_op.drop_constraint(op.f("ck_triage_records_intent"), type_="check")
        batch_op.create_check_constraint("intent", _EXPANDED_INTENTS)


def downgrade() -> None:
    with op.batch_alter_table("triage_records") as batch_op:
        batch_op.drop_constraint(op.f("ck_triage_records_intent"), type_="check")
        batch_op.create_check_constraint("intent", _ORIGINAL_INTENTS)
