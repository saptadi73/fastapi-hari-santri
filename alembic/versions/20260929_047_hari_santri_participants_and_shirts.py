"""Add order-level Hari Santri participants and shirt inventory."""
from alembic import op
import sqlalchemy as sa


revision = "202609290047"
down_revision = "202609120046"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "shirt_sizes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=24), nullable=False),
        sa.Column("label", sa.String(length=80), nullable=False),
        sa.Column("size_chart", sa.JSON(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id", "code", name="uq_shirt_size_event_code"),
    )
    op.create_table(
        "shirt_inventory",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("size_id", sa.Uuid(), nullable=False),
        sa.Column("capacity", sa.Integer(), nullable=False),
        sa.Column("reserved", sa.Integer(), nullable=False),
        sa.Column("allocated", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "capacity >= 0 AND reserved >= 0 AND allocated >= 0 AND reserved + allocated <= capacity",
            name="ck_shirt_inventory_capacity",
        ),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["size_id"], ["shirt_sizes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id", "size_id", name="uq_shirt_inventory_event_size"),
    )
    op.create_table(
        "order_participants",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("participant_number", sa.Integer(), nullable=False),
        sa.Column("full_name", sa.String(length=255), nullable=False),
        sa.Column("birth_date", sa.Date(), nullable=True),
        sa.Column("guardian_name", sa.String(length=255), nullable=True),
        sa.Column("guardian_contact", sa.String(length=40), nullable=True),
        sa.Column("activity_type", sa.String(length=30), nullable=False),
        sa.Column("shirt_size_id", sa.Uuid(), nullable=True),
        sa.Column("shirt_size_code_snapshot", sa.String(length=24), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("participant_number > 0", name="ck_order_participant_number_positive"),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["shirt_size_id"], ["shirt_sizes.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("order_id", "participant_number", name="uq_order_participant_number"),
    )
    op.create_index("ix_order_participants_order_id", "order_participants", ["order_id"])


def downgrade() -> None:
    op.drop_index("ix_order_participants_order_id", table_name="order_participants")
    op.drop_table("order_participants")
    op.drop_table("shirt_inventory")
    op.drop_table("shirt_sizes")