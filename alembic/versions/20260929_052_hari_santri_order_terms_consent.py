"""Record terms acceptance for Hari Santri orders."""
from alembic import op
import sqlalchemy as sa


revision = "202609290052"
down_revision = "202609290051"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("terms_accepted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("orders", sa.Column("terms_version", sa.String(length=40), nullable=True))


def downgrade() -> None:
    op.drop_column("orders", "terms_version")
    op.drop_column("orders", "terms_accepted_at")