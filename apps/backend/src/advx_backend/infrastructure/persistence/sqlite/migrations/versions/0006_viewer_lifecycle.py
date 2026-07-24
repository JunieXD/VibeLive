"""Persist session-scoped Viewer identity, presence, moderation and behavior.

Revision ID: 0006_viewer_lifecycle
Revises: 0005_detach_memory_evidence_events
Create Date: 2026-07-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_viewer_lifecycle"
down_revision: str | None = "0005_detach_memory_evidence_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("session_records") as batch:
        batch.add_column(sa.Column("session_seed", sa.Text(), nullable=False, server_default=""))
        batch.add_column(
            sa.Column("next_creation_ordinal", sa.Integer(), nullable=False, server_default="1")
        )
        batch.add_column(
            sa.Column(
                "target_concurrent_viewers",
                sa.Integer(),
                nullable=False,
                server_default="1",
            )
        )
        batch.add_column(
            sa.Column("population_revision", sa.Integer(), nullable=False, server_default="1")
        )
        batch.add_column(
            sa.Column("controller_state_json", sa.Text(), nullable=False, server_default="{}")
        )

    with op.batch_alter_table("session_viewer_instances") as batch:
        batch.add_column(sa.Column("username", sa.Text(), nullable=False, server_default=""))
        batch.add_column(sa.Column("avatar_seed", sa.Text(), nullable=False, server_default=""))
        batch.add_column(sa.Column("color_seed", sa.Text(), nullable=False, server_default=""))
        batch.add_column(sa.Column("locale", sa.Text(), nullable=False, server_default="zh-CN"))
        batch.add_column(
            sa.Column(
                "persona_content_hash",
                sa.Text(),
                nullable=False,
                server_default="0" * 64,
            )
        )
        batch.add_column(
            sa.Column("presence_state", sa.Text(), nullable=False, server_default="active")
        )
        batch.add_column(
            sa.Column("presence_revision", sa.Integer(), nullable=False, server_default="1")
        )
        batch.add_column(
            sa.Column("moderation_revision", sa.Integer(), nullable=False, server_default="1")
        )
        batch.add_column(
            sa.Column("behavior_revision", sa.Integer(), nullable=False, server_default="1")
        )
        batch.add_column(sa.Column("joined_at_ms", sa.Integer()))
        batch.add_column(sa.Column("last_left_at_ms", sa.Integer()))
        batch.add_column(sa.Column("join_count", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("muted_until_ms", sa.Integer()))
        batch.add_column(sa.Column("mute_reason", sa.Text()))
        batch.add_column(sa.Column("kicked_at_ms", sa.Integer()))
        batch.add_column(sa.Column("kick_reason", sa.Text()))
        batch.add_column(
            sa.Column("viewer_sequence", sa.Integer(), nullable=False, server_default="0")
        )
        batch.add_column(
            sa.Column("behavior_state_json", sa.Text(), nullable=False, server_default="{}")
        )
        batch.add_column(
            sa.Column("created_at_ms", sa.Integer(), nullable=False, server_default="0")
        )
        batch.add_column(
            sa.Column("updated_at_ms", sa.Integer(), nullable=False, server_default="0")
        )

    op.execute(
        "UPDATE session_records SET session_seed = session_id, "
        "next_creation_ordinal = COALESCE(("
        "SELECT MAX(viewers.ordinal) + 1 FROM session_viewer_instances AS viewers "
        "WHERE viewers.session_id = session_records.session_id"
        "), 1), population_revision = 1"
    )
    op.execute(
        "UPDATE session_viewer_instances SET username = display_name, "
        "avatar_seed = viewer_instance_id, color_seed = viewer_instance_id, "
        "presence_state = CASE WHEN state = 'active' THEN 'active' ELSE 'removed' END, "
        "joined_at_ms = 0, join_count = CASE WHEN state = 'active' THEN 1 ELSE 0 END"
    )


def downgrade() -> None:
    raise RuntimeError("0006 is intentionally irreversible: Viewer lifecycle data would be lost")
