"""Add Indonesian administrative region master and participant location codes."""
from alembic import op
import sqlalchemy as sa


revision = "202609290053"
down_revision = "202609290052"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "administrative_regions",
        sa.Column("code", sa.String(length=10), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("level", sa.String(length=20), nullable=False),
        sa.Column("parent_code", sa.String(length=10), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["parent_code"], ["administrative_regions.code"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("code"),
    )
    op.create_index(
        "ix_administrative_regions_level_parent",
        "administrative_regions",
        ["level", "parent_code"],
    )
    for column in ("province_code", "regency_code", "district_code", "village_code"):
        op.add_column("order_participants", sa.Column(column, sa.String(length=10), nullable=True))
        op.create_foreign_key(
            f"fk_order_participants_{column}",
            "order_participants",
            "administrative_regions",
            [column],
            ["code"],
            ondelete="RESTRICT",
        )


def downgrade() -> None:
    for column in ("village_code", "district_code", "regency_code", "province_code"):
        op.drop_constraint(f"fk_order_participants_{column}", "order_participants", type_="foreignkey")
        op.drop_column("order_participants", column)
    op.drop_index("ix_administrative_regions_level_parent", table_name="administrative_regions")
    op.drop_table("administrative_regions")