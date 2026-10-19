"""Allow Hari Santri registration orders."""
from alembic import op
import sqlalchemy as sa


revision = "202610190063"
down_revision = "202610190062"
branch_labels = None
depends_on = None


_ORDER_KIND_CHECK = "order_kind IN ('legacy', 'main_registration', 'additional', 'exhibitor', 'hari_santri_registration')"
_PREVIOUS_ORDER_KIND_CHECK = "order_kind IN ('legacy', 'main_registration', 'additional', 'exhibitor')"


def upgrade() -> None:
    op.drop_constraint("ck_orders_order_kind", "orders", type_="check")
    op.create_check_constraint("ck_orders_order_kind", "orders", _ORDER_KIND_CHECK)


def downgrade() -> None:
    bind = op.get_bind()
    hari_santri_orders = bind.execute(
        sa.text("SELECT count(*) FROM orders WHERE order_kind = 'hari_santri_registration'")
    ).scalar_one()
    if hari_santri_orders:
        raise RuntimeError(
            "Cannot downgrade while Hari Santri registration orders exist; migrate or remove them first."
        )

    op.drop_constraint("ck_orders_order_kind", "orders", type_="check")
    op.create_check_constraint("ck_orders_order_kind", "orders", _PREVIOUS_ORDER_KIND_CHECK)
