"""Store hierarchical residential region codes on user accounts."""
from alembic import op
import sqlalchemy as sa


revision = "202609290054"
down_revision = "202609290053"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for column in ("province_code", "regency_code", "district_code", "village_code"):
        op.add_column("users", sa.Column(column, sa.String(length=10), nullable=True))
        op.create_foreign_key(
            f"fk_users_{column}", "users", "administrative_regions", [column], ["code"], ondelete="RESTRICT"
        )


def downgrade() -> None:
    for column in ("village_code", "district_code", "regency_code", "province_code"):
        op.drop_constraint(f"fk_users_{column}", "users", type_="foreignkey")
        op.drop_column("users", column)