"""Add voucher expiry, admin adjustments, and exhibitor settlements."""
from alembic import op
import sqlalchemy as sa

revision = "202610040056"
down_revision = "202610040055"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("participant_vouchers", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE participant_vouchers SET expires_at = '2026-11-15 23:59:59+07:00' WHERE expires_at IS NULL")
    op.alter_column("participant_vouchers", "expires_at", nullable=False)
    op.create_table(
        "wallet_adjustments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("voucher_id", sa.Uuid(), nullable=False),
        sa.Column("amount", sa.Numeric(18, 0), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("adjusted_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["voucher_id"], ["participant_vouchers.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["adjusted_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("amount <> 0", name="ck_wallet_adjustment_amount_nonzero"),
    )
    op.create_table(
        "exhibitor_settlements",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("exhibitor_id", sa.Uuid(), nullable=False),
        sa.Column("amount", sa.Numeric(18, 0), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("payment_reference", sa.String(120), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmed_by", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(["exhibitor_id"], ["bazaar_applications.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["confirmed_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("amount > 0", name="ck_exhibitor_settlement_amount_positive"),
        sa.CheckConstraint("status IN ('pending', 'confirmed')", name="ck_exhibitor_settlement_status"),
    )


def downgrade() -> None:
    op.drop_table("exhibitor_settlements")
    op.drop_table("wallet_adjustments")
    op.drop_column("participant_vouchers", "expires_at")
