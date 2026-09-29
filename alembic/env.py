from logging.config import fileConfig
from sqlalchemy import pool
from sqlalchemy import engine_from_config
from alembic import context

from app.core.config import get_settings
from app.core.database import Base
from app.modules.events.models import Event
from app.modules.participants.models import ParticipantProfile
from app.modules.check_ins.models import CheckIn
from app.modules.speakers.models import Speaker, EventSpeaker
from app.modules.sessions.models import EventSession
from app.modules.registrations.models import Registration
from app.modules.tickets.models import Ticket, QRToken
from app.modules.payments.models import Order, Payment, PaymentWebhookEvent, DirectDebitBinding, PaymentChannel
from app.modules.users.models import User
from app.modules.business_matching import models as business_matching_models
from app.modules.email_notifications import models as email_notification_models
from app.modules.iwbif import models as iwbif_models
from app.modules.admin_content import models as admin_content_models
from app.modules.committee import models as committee_models
from app.modules.hari_santri import models as hari_santri_models

config = context.config
fileConfig(config.config_file_name)

target_metadata = Base.metadata


def get_migration_database_url() -> str:
    database_url = get_settings().DATABASE_URL
    return database_url.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1)


def run_migrations_offline() -> None:
    config.set_main_option("sqlalchemy.url", get_migration_database_url())
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    config.set_main_option("sqlalchemy.url", get_migration_database_url())
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
