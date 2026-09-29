"""Add Payment Portal references and callback deduplication."""
from alembic import op
import sqlalchemy as sa


revision = "202609290048"
down_revision = "202609290047"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "hari_santri_payments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("payment_portal_id", sa.String(length=64), nullable=True),
        sa.Column("payment_no", sa.String(length=64), nullable=True),
        sa.Column("reference_id", sa.String(length=100), nullable=False),
        sa.Column("payment_url", sa.Text(), nullable=True),
        sa.Column("amount", sa.Numeric(18, 0), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("order_id", name="uq_hari_santri_payments_order_id"),
        sa.UniqueConstraint("payment_portal_id", name="uq_hari_santri_payments_portal_id"),
        sa.UniqueConstraint("payment_no", name="uq_hari_santri_payments_payment_no"),
        sa.UniqueConstraint("reference_id", name="uq_hari_santri_payments_reference_id"),
    )
    op.create_table(
        "hari_santri_callback_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.String(length=150), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("processing_status", sa.String(length=20), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id", name="uq_hari_santri_callback_event_id"),
    )


def downgrade() -> None:
    op.drop_table("hari_santri_callback_events")
    op.drop_table("hari_santri_payments")