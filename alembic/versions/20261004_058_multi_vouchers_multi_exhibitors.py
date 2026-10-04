"""Allow multiple vouchers per participant and multiple stalls per account."""
from alembic import op

revision = "202610040058"
down_revision = "202610040057"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("participant_wallets_participant_id_key", "participant_wallets", type_="unique")
    op.drop_constraint("participant_vouchers_participant_id_key", "participant_vouchers", type_="unique")
    op.drop_constraint("uq_bazaar_application_event_user", "bazaar_applications", type_="unique")


def downgrade() -> None:
    op.create_unique_constraint("uq_bazaar_application_event_user", "bazaar_applications", ["event_id", "user_id"])
    op.create_unique_constraint("participant_vouchers_participant_id_key", "participant_vouchers", ["participant_id"])
    op.create_unique_constraint("participant_wallets_participant_id_key", "participant_wallets", ["participant_id"])
