"""Add Hari Santri donations paid through Payment Portal."""
from alembic import op
import sqlalchemy as sa


revision = "202610190062"
down_revision = "202610120061"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "hari_santri_donations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.String(length=100), nullable=False),
        sa.Column("reference_id", sa.String(length=100), nullable=False),
        sa.Column("donor_name", sa.String(length=180), nullable=False),
        sa.Column("donor_email", sa.String(length=255), nullable=False),
        sa.Column("amount", sa.Numeric(precision=18, scale=0), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("payment_portal_id", sa.String(length=64), nullable=True),
        sa.Column("payment_no", sa.String(length=64), nullable=True),
        sa.Column("payment_url", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("request_id", name="uq_hari_santri_donations_request_id"),
        sa.UniqueConstraint("reference_id", name="uq_hari_santri_donations_reference_id"),
        sa.UniqueConstraint("payment_portal_id", name="uq_hari_santri_donations_payment_portal_id"),
        sa.UniqueConstraint("payment_no", name="uq_hari_santri_donations_payment_no"),
        sa.CheckConstraint("amount > 0", name="ck_hari_santri_donations_positive_amount"),
    )


def downgrade() -> None:
    op.drop_table("hari_santri_donations")
