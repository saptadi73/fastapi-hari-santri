"""Add Hari Santri bazaar tenant applications."""
from alembic import op
import sqlalchemy as sa


revision = "202609290051"
down_revision = "202609290050"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bazaar_applications",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("business_name", sa.String(length=180), nullable=False),
        sa.Column("representative_name", sa.String(length=180), nullable=False),
        sa.Column("contact_email", sa.String(length=255), nullable=False),
        sa.Column("contact_phone", sa.String(length=40), nullable=False),
        sa.Column("category", sa.String(length=30), nullable=False),
        sa.Column("product_summary", sa.Text(), nullable=False),
        sa.Column("stall_needs", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("organizer_notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id", "user_id", name="uq_bazaar_application_event_user"),
    )
    op.create_index("ix_bazaar_applications_event_status", "bazaar_applications", ["event_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_bazaar_applications_event_status", table_name="bazaar_applications")
    op.drop_table("bazaar_applications")