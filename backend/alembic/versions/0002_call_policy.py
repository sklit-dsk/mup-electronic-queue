"""Persist call attempts and the time of the last call."""

from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("tickets", sa.Column("call_attempts", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("tickets", sa.Column("last_called_at", sa.DateTime(timezone=True), nullable=True))
    # Existing called tickets get a fresh grace period; historical call totals remain intact.
    op.execute(
        "UPDATE tickets SET call_attempts = 1, last_called_at = CURRENT_TIMESTAMP WHERE state = 'CALLED'"
    )


def downgrade():
    op.drop_column("tickets", "last_called_at")
    op.drop_column("tickets", "call_attempts")
