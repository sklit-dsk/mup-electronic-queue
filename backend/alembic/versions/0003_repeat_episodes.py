"""Keep repeat visits separate from the original completed ticket."""

from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.create_unique_constraint("ticket_id_day_unique", "tickets", ["id", "day_id"])
    op.create_table(
        "service_episodes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("ticket_id", sa.String(36), nullable=False),
        sa.Column("day_id", sa.String(50), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("window", sa.Integer(), nullable=True),
        sa.Column("reason", sa.String(300), nullable=False),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("original_match", sa.Boolean(), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verified_by", sa.String(36), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("calls", sa.Integer(), nullable=False),
        sa.Column("call_attempts", sa.Integer(), nullable=False),
        sa.Column("last_called_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["ticket_id", "day_id"], ["tickets.id", "tickets.day_id"]),
        sa.UniqueConstraint("ticket_id", "number"),
        sa.CheckConstraint("number >= 1"),
        sa.CheckConstraint("length(trim(reason)) > 0"),
        sa.CheckConstraint(
            "state IN ('CALLED','IN_SERVICE','SKIPPED','COMPLETED','NO_SHOW','REJECTED','CANCELLED')"
        ),
        sa.CheckConstraint("state NOT IN ('CALLED','IN_SERVICE') OR \"window\" IS NOT NULL"),
        sa.CheckConstraint('"window" IS NULL OR "window" BETWEEN 1 AND 3'),
    )
    op.create_index("ix_service_episodes_ticket_id", "service_episodes", ["ticket_id"])
    op.create_index("ix_service_episodes_day_id", "service_episodes", ["day_id"])
    op.create_index(
        "one_active_repeat_per_ticket",
        "service_episodes",
        ["ticket_id"],
        unique=True,
        postgresql_where=sa.text("state IN ('CALLED','IN_SERVICE','SKIPPED')"),
    )
    op.create_index(
        "one_repeat_per_window",
        "service_episodes",
        ["day_id", "window"],
        unique=True,
        postgresql_where=sa.text("state IN ('CALLED','IN_SERVICE')"),
    )


def downgrade():
    # Do not silently erase completed repeat visits during a rollback.
    if op.get_bind().execute(sa.text("SELECT EXISTS (SELECT 1 FROM service_episodes)")).scalar():
        raise RuntimeError(
            "Cannot downgrade 0003 while repeat-visit history exists; preserve it before rollback"
        )
    op.drop_table("service_episodes")
    op.drop_constraint("ticket_id_day_unique", "tickets", type_="unique")
