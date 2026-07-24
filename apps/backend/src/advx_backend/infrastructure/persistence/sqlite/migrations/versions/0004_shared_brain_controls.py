"""Add durable ModeMeme idempotency keys.

Revision ID: 0004_shared_brain_controls
Revises: 0003_mode_meme_candidates
Create Date: 2026-07-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_shared_brain_controls"
down_revision: str | None = "0003_mode_meme_candidates"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("mode_meme_candidates") as batch:
        batch.add_column(sa.Column("idempotency_key", sa.Text(), nullable=True))
    op.execute(
        "UPDATE mode_meme_candidates "
        "SET idempotency_key = candidate_id "
        "WHERE idempotency_key IS NULL"
    )
    with op.batch_alter_table("mode_meme_candidates") as batch:
        batch.alter_column("idempotency_key", existing_type=sa.Text(), nullable=False)
        batch.create_unique_constraint(
            "uq_mode_meme_candidates_namespace_idempotency",
            ["mode_namespace", "idempotency_key"],
        )


def downgrade() -> None:
    with op.batch_alter_table("mode_meme_candidates") as batch:
        batch.drop_constraint(
            "uq_mode_meme_candidates_namespace_idempotency",
            type_="unique",
        )
        batch.drop_column("idempotency_key")
