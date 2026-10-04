"""Add participant voucher and exhibitor wallet balances."""
from alembic import op
import sqlalchemy as sa

revision = "202610040055"
down_revision = "202609290054"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "participant_wallets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("participant_id", sa.Uuid(), nullable=False),
        sa.Column("balance", sa.Numeric(18, 0), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["participant_id"], ["order_participants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("participant_id"),
        sa.CheckConstraint("balance >= 0", name="ck_participant_wallet_balance_nonnegative"),
    )
    op.create_table(
        "exhibitor_wallets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("exhibitor_id", sa.Uuid(), nullable=False),
        sa.Column("balance", sa.Numeric(18, 0), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["exhibitor_id"], ["bazaar_applications.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("exhibitor_id"),
        sa.CheckConstraint("balance >= 0", name="ck_exhibitor_wallet_balance_nonnegative"),
    )
    op.create_table(
        "participant_vouchers",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("participant_id", sa.Uuid(), nullable=False),
        sa.Column("wallet_id", sa.Uuid(), nullable=False),
        sa.Column("qr_token_hash", sa.String(64), nullable=False),
        sa.Column("initial_balance", sa.Numeric(18, 0), nullable=False),
        sa.Column("status", sa.String(20), server_default="active", nullable=False),
        sa.Column("issued_by", sa.Uuid(), nullable=True),
        sa.Column("issued_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["participant_id"], ["order_participants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["wallet_id"], ["participant_wallets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["issued_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("participant_id"), sa.UniqueConstraint("wallet_id"),
        sa.UniqueConstraint("qr_token_hash", name="uq_participant_voucher_qr_hash"),
    )
    op.create_table(
        "wallet_transfers",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.String(100), nullable=False),
        sa.Column("voucher_id", sa.Uuid(), nullable=False),
        sa.Column("participant_id", sa.Uuid(), nullable=False),
        sa.Column("exhibitor_id", sa.Uuid(), nullable=False),
        sa.Column("amount", sa.Numeric(18, 0), nullable=False),
        sa.Column("scanned_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["voucher_id"], ["participant_vouchers.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["participant_id"], ["order_participants.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["exhibitor_id"], ["bazaar_applications.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["scanned_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("request_id", name="uq_wallet_transfer_request_id"),
        sa.CheckConstraint("amount > 0", name="ck_wallet_transfer_amount_positive"),
    )


def downgrade() -> None:
    op.drop_table("wallet_transfers")
    op.drop_table("participant_vouchers")
    op.drop_table("exhibitor_wallets")
    op.drop_table("participant_wallets")
