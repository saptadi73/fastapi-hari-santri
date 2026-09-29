"""Add Hari Santri QR tickets and one-time check-ins."""
from alembic import op
import sqlalchemy as sa


revision = "202609290049"
down_revision = "202609290048"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "hari_santri_tickets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("participant_id", sa.Uuid(), nullable=False),
        sa.Column("ticket_number", sa.String(length=40), nullable=False),
        sa.Column("qr_token_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("checked_in_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["participant_id"], ["order_participants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("participant_id", name="uq_hari_santri_ticket_participant"),
        sa.UniqueConstraint("ticket_number", name="uq_hari_santri_ticket_number"),
        sa.UniqueConstraint("qr_token_hash", name="uq_hari_santri_ticket_token_hash"),
    )
    op.create_table(
        "hari_santri_checkins",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("ticket_id", sa.Uuid(), nullable=False),
        sa.Column("scanned_by", sa.Uuid(), nullable=True),
        sa.Column("scanned_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("result", sa.String(length=20), nullable=False),
        sa.ForeignKeyConstraint(["ticket_id"], ["hari_santri_tickets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["scanned_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ticket_id", name="uq_hari_santri_checkin_ticket"),
    )


def downgrade() -> None:
    op.drop_table("hari_santri_checkins")
    op.drop_table("hari_santri_tickets")