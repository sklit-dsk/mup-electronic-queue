"""Preserve passport revisions and review access changes explicitly."""

from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("documents", sa.Column("version", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("documents", sa.Column("revision", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("tickets", sa.Column("document_revision", sa.Integer(), nullable=False, server_default="1"))
    op.create_table(
        "document_revisions",
        sa.Column("document_id", sa.String(36), sa.ForeignKey("documents.id"), primary_key=True),
        sa.Column("revision", sa.Integer(), primary_key=True),
        sa.Column("fingerprint", sa.String(64), nullable=False, unique=True),
        sa.Column("encrypted_fields", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id"), nullable=True),
    )
    op.execute(
        "INSERT INTO document_revisions (document_id,revision,fingerprint,encrypted_fields,created_at) SELECT id,1,fingerprint,encrypted_fields,created_at FROM documents"
    )
    op.create_table(
        "document_requests",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("requester_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("office_id", sa.String(30), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("upload_id", sa.String(36), sa.ForeignKey("uploads.id"), nullable=False),
        sa.Column("document_id", sa.String(36), sa.ForeignKey("documents.id"), nullable=True),
        sa.Column("base_version", sa.Integer(), nullable=True),
        sa.Column("previous_owner_id", sa.String(36), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by", sa.String(36), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("reason", sa.String(300), nullable=False),
        sa.CheckConstraint("kind IN ('RECOVER','REPLACE')"),
        sa.CheckConstraint("state IN ('PENDING','APPROVED','REJECTED','CANCELLED')"),
    )
    op.create_index("ix_document_requests_requester_id", "document_requests", ["requester_id"])
    op.create_index(
        "one_pending_document_request",
        "document_requests",
        ["requester_id", "kind", "upload_id"],
        unique=True,
        postgresql_where=sa.text("state = 'PENDING'"),
    )
    op.add_column(
        "outbox", sa.Column("document_id", sa.String(36), sa.ForeignKey("documents.id"), nullable=True)
    )
    op.add_column("outbox", sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_outbox_document_id", "outbox", ["document_id"])


def downgrade():
    changed = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT EXISTS (SELECT 1 FROM document_requests) OR EXISTS (SELECT 1 FROM documents WHERE version > 1)"
            )
        )
        .scalar()
    )
    if changed:
        raise RuntimeError("Cannot downgrade 0004 while document-access history exists")
    op.drop_index("ix_outbox_document_id", table_name="outbox")
    op.drop_column("outbox", "cancelled_at")
    op.drop_column("outbox", "document_id")
    op.drop_table("document_requests")
    op.drop_table("document_revisions")
    op.drop_column("tickets", "document_revision")
    op.drop_column("documents", "version")
    op.drop_column("documents", "revision")
