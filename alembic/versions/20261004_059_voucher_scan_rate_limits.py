"""Store voucher scan rate limits in PostgreSQL for all API workers."""
from alembic import op
import sqlalchemy as sa


revision = "202610040059"
down_revision = "202610040058"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "voucher_scan_rate_limits",
        sa.Column("subject_key", sa.String(length=255), nullable=False),
        sa.Column("window_start", sa.BigInteger(), nullable=False),
        sa.Column("request_count", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("request_count > 0", name="ck_voucher_scan_rate_limits_request_count_positive"),
        sa.PrimaryKeyConstraint("subject_key", "window_start"),
    )


def downgrade() -> None:
    op.drop_table("voucher_scan_rate_limits")
