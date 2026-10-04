"""Add audit trail for Hari Santri voucher wallet operations."""
from alembic import op
import sqlalchemy as sa

revision = "202610040057"
down_revision = "202610040056"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "hari_santri_audit_logs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.String(length=80), nullable=False),
        sa.Column("entity_type", sa.String(length=80), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_hari_santri_audit_logs_created_at", "hari_santri_audit_logs", ["created_at"])
    op.create_index("ix_hari_santri_audit_logs_entity", "hari_santri_audit_logs", ["entity_type", "entity_id"])


def downgrade() -> None:
    op.drop_index("ix_hari_santri_audit_logs_entity", table_name="hari_santri_audit_logs")
    op.drop_index("ix_hari_santri_audit_logs_created_at", table_name="hari_santri_audit_logs")
    op.drop_table("hari_santri_audit_logs")
