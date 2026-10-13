"""Set every existing account preference to Indonesian."""
from alembic import op


revision = "202610120061"
down_revision = "202610070060"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE users SET preferred_locale = 'id' WHERE preferred_locale <> 'id'")
    op.drop_constraint("ck_users_preferred_locale", "users", type_="check")
    op.create_check_constraint("ck_users_preferred_locale", "users", "preferred_locale = 'id'")


def downgrade() -> None:
    op.drop_constraint("ck_users_preferred_locale", "users", type_="check")
    op.create_check_constraint("ck_users_preferred_locale", "users", "preferred_locale IN ('id', 'en')")
