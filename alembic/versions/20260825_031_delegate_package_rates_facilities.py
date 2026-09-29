"""delegate package rates, facilities, and registration selections

Revision ID: 202608250031
Revises: 202608250030
"""
import sqlalchemy as sa
from alembic import op

revision = "202608250031"
down_revision = "202608250030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE delegate_packages ADD COLUMN IF NOT EXISTS package_type VARCHAR(20) NOT NULL DEFAULT 'main'")
    op.execute("ALTER TABLE delegate_packages ADD COLUMN IF NOT EXISTS selection_mode VARCHAR(20) NOT NULL DEFAULT 'required_one'")
    op.execute("ALTER TABLE delegate_packages ADD COLUMN IF NOT EXISTS description TEXT")
    op.execute("ALTER TABLE delegate_packages ADD COLUMN IF NOT EXISTS display_order INTEGER NOT NULL DEFAULT 0")
    op.create_table(
        "delegate_package_rates",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("delegate_package_id", sa.Uuid(), sa.ForeignKey("delegate_packages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("occupancy_type", sa.String(20), nullable=False), sa.Column("name", sa.String(120), nullable=False),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False), sa.Column("currency", sa.String(3), server_default="USD", nullable=False),
        sa.Column("payment_amount_idr", sa.Numeric(18, 2)), sa.Column("is_default", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False), sa.Column("valid_from", sa.DateTime(timezone=True)),
        sa.Column("valid_until", sa.DateTime(timezone=True)), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("delegate_package_id", "occupancy_type", name="uq_delegate_package_occupancy"),
    )
    op.create_table(
        "delegate_package_facilities",
        sa.Column("id", sa.Uuid(), primary_key=True), sa.Column("delegate_package_id", sa.Uuid(), sa.ForeignKey("delegate_packages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False), sa.Column("description", sa.Text()), sa.Column("quantity", sa.Integer()), sa.Column("unit", sa.String(40)),
        sa.Column("pricing_mode", sa.String(30), server_default="included", nullable=False), sa.Column("sharing_amount", sa.Numeric(18, 2)),
        sa.Column("single_amount", sa.Numeric(18, 2)), sa.Column("currency", sa.String(3), server_default="USD", nullable=False),
        sa.Column("display_order", sa.Integer(), server_default="0", nullable=False), sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "delegate_registration_package_selections",
        sa.Column("id", sa.Uuid(), primary_key=True), sa.Column("registration_id", sa.Uuid(), sa.ForeignKey("registrations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("delegate_package_id", sa.Uuid(), sa.ForeignKey("delegate_packages.id"), nullable=False), sa.Column("package_rate_id", sa.Uuid(), sa.ForeignKey("delegate_package_rates.id"), nullable=False),
        sa.Column("selection_role", sa.String(20), nullable=False), sa.Column("occupancy_type", sa.String(20), nullable=False),
        sa.Column("package_code", sa.String(30), nullable=False), sa.Column("package_name", sa.String(160), nullable=False), sa.Column("rate_name", sa.String(120), nullable=False),
        sa.Column("selected_amount", sa.Numeric(18, 2), nullable=False), sa.Column("selected_currency", sa.String(3), nullable=False),
        sa.Column("selected_payment_amount", sa.Numeric(18, 2), nullable=False), sa.Column("payment_currency", sa.String(3), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("registration_id", "delegate_package_id", name="uq_registration_delegate_package"),
    )
    op.execute("ALTER TABLE products ADD COLUMN IF NOT EXISTS delegate_package_rate_id UUID")
    op.execute("ALTER TABLE order_items ADD COLUMN IF NOT EXISTS metadata_json JSON NOT NULL DEFAULT '{}'::json")
    op.create_foreign_key("fk_product_delegate_package_rate", "products", "delegate_package_rates", ["delegate_package_rate_id"], ["id"], ondelete="CASCADE")
    op.create_unique_constraint("uq_product_delegate_package_rate", "products", ["delegate_package_rate_id"])
    # Preserve existing packages as their default sharing rates and keep current store products linked.
    op.execute("""
        INSERT INTO delegate_package_rates (id, delegate_package_id, occupancy_type, name, amount, currency, payment_amount_idr, is_default, is_active)
        SELECT gen_random_uuid(), id, 'sharing', 'Twin Sharing Basis', amount, currency, payment_amount_idr, true, is_active FROM delegate_packages
    """)
    op.execute("""
        UPDATE products p SET delegate_package_rate_id = r.id
        FROM delegate_package_rates r
        WHERE (p.metadata_json->>'delegate_package_id') ~* '^[0-9a-f-]{36}$'
          AND (p.metadata_json->>'delegate_package_id')::uuid = r.delegate_package_id
          AND r.occupancy_type = 'sharing'
    """)


def downgrade() -> None:
    op.drop_constraint("uq_product_delegate_package_rate", "products", type_="unique")
    op.drop_constraint("fk_product_delegate_package_rate", "products", type_="foreignkey")
    op.drop_column("products", "delegate_package_rate_id")
    op.drop_column("order_items", "metadata_json")
    op.drop_table("delegate_registration_package_selections")
    op.drop_table("delegate_package_facilities")
    op.drop_table("delegate_package_rates")
    op.drop_column("delegate_packages", "display_order"); op.drop_column("delegate_packages", "description")
    op.drop_column("delegate_packages", "selection_mode")
