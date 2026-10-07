"""Add durable callback retry outbox for Hari Santri operations."""
from alembic import op
import sqlalchemy as sa


revision = "202610070060"
down_revision = "202610040059"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "hari_santri_outbox_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("request_id", sa.String(length=100), nullable=False),
        sa.Column("dedupe_key", sa.String(length=255), nullable=False),
        sa.Column("aggregate_id", sa.Uuid(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default="8", nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_error_code", sa.String(length=100), nullable=True),
        sa.Column("last_error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dedupe_key", name="uq_hari_santri_outbox_dedupe_key"),
        sa.CheckConstraint("attempts >= 0 AND max_attempts > 0", name="ck_hari_santri_outbox_attempts"),
        sa.CheckConstraint("status IN ('pending', 'processing', 'completed', 'dead')", name="ck_hari_santri_outbox_status"),
    )
    op.create_index("ix_hari_santri_outbox_event_type", "hari_santri_outbox_events", ["event_type"])
    op.create_index("ix_hari_santri_outbox_status", "hari_santri_outbox_events", ["status"])
    op.create_index("ix_hari_santri_outbox_available_at", "hari_santri_outbox_events", ["available_at"])


def downgrade() -> None:
    op.drop_index("ix_hari_santri_outbox_available_at", table_name="hari_santri_outbox_events")
    op.drop_index("ix_hari_santri_outbox_status", table_name="hari_santri_outbox_events")
    op.drop_index("ix_hari_santri_outbox_event_type", table_name="hari_santri_outbox_events")
    op.drop_table("hari_santri_outbox_events")
