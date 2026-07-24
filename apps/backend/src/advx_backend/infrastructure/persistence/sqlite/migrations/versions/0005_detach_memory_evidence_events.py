"""Keep memory evidence snapshots after bounded room event pruning.

Revision ID: 0005_detach_memory_evidence_events
Revises: 0004_shared_brain_controls
Create Date: 2026-07-24
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005_detach_memory_evidence_events"
down_revision: str | None = "0004_shared_brain_controls"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("room_memory_evidence") as batch:
        batch.drop_constraint(
            "fk_room_memory_evidence_event_id_room_events",
            type_="foreignkey",
        )


def downgrade() -> None:
    raise RuntimeError(
        "0005 is intentionally irreversible: pruned room events may have durable "
        "memory evidence snapshots"
    )
