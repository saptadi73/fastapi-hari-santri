"""Switch active locales to Indonesian and English without relabeling Chinese content."""
from alembic import op
import sqlalchemy as sa


revision = "202609290050"
down_revision = "202609290049"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_users_preferred_locale", "users", type_="check")
    op.execute("UPDATE users SET preferred_locale = 'id' WHERE preferred_locale = 'zh-CN'")
    op.create_check_constraint("ck_users_preferred_locale", "users", "preferred_locale IN ('id', 'en')")

    op.drop_constraint("ck_content_translation_locale", "content_translations", type_="check")
    op.create_check_constraint(
        "ck_content_translation_locale",
        "content_translations",
        "locale IN ('id', 'en', 'zh-CN')",
    )
    op.drop_constraint("ck_email_template_locale", "email_notification_templates", type_="check")
    op.create_check_constraint(
        "ck_email_template_locale",
        "email_notification_templates",
        "locale IN ('id', 'en', 'zh-CN')",
    )
    op.drop_constraint("ck_email_log_locale", "email_notification_logs", type_="check")
    op.create_check_constraint(
        "ck_email_log_locale",
        "email_notification_logs",
        "locale IN ('id', 'en', 'zh-CN')",
    )


def downgrade() -> None:
    raise RuntimeError(
        "Locale cutover is forward-only: restore a database snapshot to roll back without relabeling Indonesian content."
    )