"""Initial queue schema, frozen at revision 0001."""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # Schema is embedded below before packaging; no live Base.metadata dependency.
    from pathlib import Path
    connection = op.get_bind()
    sql = (Path(__file__).parent / "0001_schema.sql").read_text()
    for statement in sql.split(";\n"):
        if statement.strip():
            connection.execute(sa.text(statement))


def downgrade():
    for table in ("outbox", "idempotency", "audit", "waiting", "tickets", "day_claims", "auth_throttles",
                  "seats", "office_days", "uploads", "documents", "otp_challenges", "sessions", "users"):
        op.drop_table(table)
