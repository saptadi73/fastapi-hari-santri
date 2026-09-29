import asyncio
import logging
import re
from datetime import datetime, timezone
from email.message import EmailMessage
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.core.database import AsyncSessionFactory
from app.core.email import _send_message, frontend_login_url
from app.core.i18n import DEFAULT_LOCALE, SUPPORTED_LOCALES, normalize_locale
from app.modules.email_notifications.models import EmailNotificationLog, EmailNotificationPreference, EmailNotificationTemplate
from app.modules.users.models import User

logger = logging.getLogger(__name__)
TOKEN_PATTERN = re.compile(r"{{\s*([a-zA-Z][a-zA-Z0-9_]*)\s*}}")

TRIGGER_VARIABLES = {
    "account_registered": ["participant_name", "event_name", "login_url"],
    "registration_submitted": ["participant_name", "event_name", "registration_number", "login_url"],
    "delegate_package_selected": ["participant_name", "event_name", "package_name", "package_code", "amount", "currency", "login_url"],
    "exhibitor_package_selected": ["participant_name", "event_name", "package_name", "package_code", "amount", "currency", "login_url"],
    "payment_confirmed": ["participant_name", "event_name", "order_number", "amount", "currency", "paid_at", "login_url"],
    "business_matching_profile_saved": ["participant_name", "event_name", "login_url"],
    "meeting_requested": ["participant_name", "event_name", "counterparty_name", "meeting_topic", "login_url"],
    "meeting_accepted": ["participant_name", "event_name", "counterparty_name", "meeting_topic", "login_url"],
    "meeting_confirmed": ["participant_name", "event_name", "counterparty_name", "meeting_topic", "meeting_schedule", "meeting_venue", "login_url"],
    "meeting_declined": ["participant_name", "event_name", "counterparty_name", "meeting_topic", "login_url"],
    "meeting_cancelled": ["participant_name", "event_name", "counterparty_name", "meeting_topic", "login_url"],
    "meeting_reschedule_requested": ["participant_name", "event_name", "counterparty_name", "meeting_topic", "login_url"],
}

DEFAULT_TEMPLATES = {
    "account_registered": ("Welcome to {{ event_name }}", "Dear {{ participant_name }},\n\nYour account for {{ event_name }} has been created successfully. Please sign in to complete your registration.\n\nSign in: {{ login_url }}\n\nBest regards,\nThe IWBIF Team"),
    "registration_submitted": ("Registration {{ registration_number }} received", "Dear {{ participant_name }},\n\nWe have received your registration for {{ event_name }}.\n\nRegistration number: {{ registration_number }}\n\nYou can review your registration status here: {{ login_url }}\n\nBest regards,\nThe IWBIF Team"),
    "delegate_package_selected": ("Delegate package selected: {{ package_name }}", "Dear {{ participant_name }},\n\nYou have selected {{ package_name }} ({{ package_code }}) for {{ event_name }}.\n\nPackage price: {{ currency }} {{ amount }}\n\nReview your selection: {{ login_url }}\n\nBest regards,\nThe IWBIF Team"),
    "exhibitor_package_selected": ("Exhibitor package selected: {{ package_name }}", "Dear {{ participant_name }},\n\nYou have selected the exhibitor package {{ package_name }} ({{ package_code }}) for {{ event_name }}.\n\nPackage price: {{ currency }} {{ amount }}\n\nReview your selection: {{ login_url }}\n\nBest regards,\nThe IWBIF Team"),
    "payment_confirmed": ("Payment confirmed for order {{ order_number }}", "Dear {{ participant_name }},\n\nYour payment for order {{ order_number }} has been confirmed.\n\nAmount paid: {{ currency }} {{ amount }}\nConfirmed at: {{ paid_at }}\n\nView your order: {{ login_url }}\n\nBest regards,\nThe IWBIF Team"),
    "business_matching_profile_saved": ("Your business matching profile is ready", "Dear {{ participant_name }},\n\nYour business matching profile for {{ event_name }} has been saved successfully. You can now discover relevant participants and business opportunities.\n\nOpen business matching: {{ login_url }}\n\nBest regards,\nThe IWBIF Team"),
    "meeting_requested": ("New business matching meeting request", "Dear {{ participant_name }},\n\n{{ counterparty_name }} has invited you to a business matching meeting at {{ event_name }}.\n\nTopic: {{ meeting_topic }}\n\nReview and respond: {{ login_url }}\n\nBest regards,\nThe IWBIF Team"),
    "meeting_accepted": ("Your meeting request has been accepted", "Dear {{ participant_name }},\n\n{{ counterparty_name }} has accepted your meeting request.\n\nTopic: {{ meeting_topic }}\n\nContinue to scheduling: {{ login_url }}\n\nBest regards,\nThe IWBIF Team"),
    "meeting_confirmed": ("Business matching meeting confirmed", "Dear {{ participant_name }},\n\nYour meeting with {{ counterparty_name }} has been confirmed.\n\nTopic: {{ meeting_topic }}\nSchedule: {{ meeting_schedule }}\nVenue: {{ meeting_venue }}\n\nView meeting details: {{ login_url }}\n\nBest regards,\nThe IWBIF Team"),
    "meeting_declined": ("Business matching meeting request declined", "Dear {{ participant_name }},\n\n{{ counterparty_name }} has declined the meeting request regarding {{ meeting_topic }}.\n\nExplore other business matches: {{ login_url }}\n\nBest regards,\nThe IWBIF Team"),
    "meeting_cancelled": ("Business matching meeting cancelled", "Dear {{ participant_name }},\n\nYour meeting with {{ counterparty_name }} regarding {{ meeting_topic }} has been cancelled.\n\nView your meeting schedule: {{ login_url }}\n\nBest regards,\nThe IWBIF Team"),
    "meeting_reschedule_requested": ("Meeting reschedule requested", "Dear {{ participant_name }},\n\n{{ counterparty_name }} has requested a new schedule for your meeting regarding {{ meeting_topic }}.\n\nReview the request: {{ login_url }}\n\nBest regards,\nThe IWBIF Team"),
}

ZH_CN_DEFAULT_TEMPLATES = {
    "account_registered": ("欢迎参加 {{ event_name }}", "尊敬的 {{ participant_name }}：\n\n您已成功创建 {{ event_name }} 账户。请登录并完成注册。\n\n登录：{{ login_url }}\n\n此致\nIWBIF 团队"),
    "registration_submitted": ("已收到注册 {{ registration_number }}", "尊敬的 {{ participant_name }}：\n\n我们已收到您参加 {{ event_name }} 的注册。\n\n注册编号：{{ registration_number }}\n\n查看注册状态：{{ login_url }}\n\n此致\nIWBIF 团队"),
    "delegate_package_selected": ("已选择代表套餐：{{ package_name }}", "尊敬的 {{ participant_name }}：\n\n您已选择 {{ event_name }} 的 {{ package_name }}（{{ package_code }}）。\n\n套餐价格：{{ currency }} {{ amount }}\n\n查看选择：{{ login_url }}\n\n此致\nIWBIF 团队"),
    "exhibitor_package_selected": ("已选择参展商套餐：{{ package_name }}", "尊敬的 {{ participant_name }}：\n\n您已选择 {{ event_name }} 的参展商套餐 {{ package_name }}（{{ package_code }}）。\n\n套餐价格：{{ currency }} {{ amount }}\n\n查看选择：{{ login_url }}\n\n此致\nIWBIF 团队"),
    "payment_confirmed": ("订单 {{ order_number }} 付款已确认", "尊敬的 {{ participant_name }}：\n\n订单 {{ order_number }} 的付款已确认。\n\n付款金额：{{ currency }} {{ amount }}\n确认时间：{{ paid_at }}\n\n查看订单：{{ login_url }}\n\n此致\nIWBIF 团队"),
    "business_matching_profile_saved": ("您的商务配对资料已就绪", "尊敬的 {{ participant_name }}：\n\n您在 {{ event_name }} 的商务配对资料已保存。\n\n打开商务配对：{{ login_url }}\n\n此致\nIWBIF 团队"),
    "meeting_requested": ("新的商务配对会议邀请", "尊敬的 {{ participant_name }}：\n\n{{ counterparty_name }} 邀请您在 {{ event_name }} 参加商务配对会议。\n\n主题：{{ meeting_topic }}\n\n查看并回复：{{ login_url }}\n\n此致\nIWBIF 团队"),
    "meeting_accepted": ("您的会议邀请已接受", "尊敬的 {{ participant_name }}：\n\n{{ counterparty_name }} 已接受您的会议邀请。\n\n主题：{{ meeting_topic }}\n\n继续安排时间：{{ login_url }}\n\n此致\nIWBIF 团队"),
    "meeting_confirmed": ("商务配对会议已确认", "尊敬的 {{ participant_name }}：\n\n您与 {{ counterparty_name }} 的会议已确认。\n\n主题：{{ meeting_topic }}\n时间：{{ meeting_schedule }}\n地点：{{ meeting_venue }}\n\n查看详情：{{ login_url }}\n\n此致\nIWBIF 团队"),
    "meeting_declined": ("商务配对会议邀请已拒绝", "尊敬的 {{ participant_name }}：\n\n{{ counterparty_name }} 已拒绝主题为 {{ meeting_topic }} 的会议邀请。\n\n查找其他配对：{{ login_url }}\n\n此致\nIWBIF 团队"),
    "meeting_cancelled": ("商务配对会议已取消", "尊敬的 {{ participant_name }}：\n\n您与 {{ counterparty_name }} 关于 {{ meeting_topic }} 的会议已取消。\n\n查看会议安排：{{ login_url }}\n\n此致\nIWBIF 团队"),
    "meeting_reschedule_requested": ("会议改期请求", "尊敬的 {{ participant_name }}：\n\n{{ counterparty_name }} 请求为主题 {{ meeting_topic }} 的会议重新安排时间。\n\n查看请求：{{ login_url }}\n\n此致\nIWBIF 团队"),
}

ID_DEFAULT_TEMPLATES = {
    "account_registered": ("Selamat datang di {{ event_name }}", "Assalamu’alaikum {{ participant_name }},\n\nAkun Anda untuk {{ event_name }} berhasil dibuat. Silakan masuk untuk melanjutkan pendaftaran.\n\nMasuk: {{ login_url }}\n\nSalam,\nPanitia Hari Santri 2026"),
    "registration_submitted": ("Pendaftaran {{ registration_number }} diterima", "Assalamu’alaikum {{ participant_name }},\n\nPendaftaran Anda untuk {{ event_name }} telah kami terima.\n\nNomor pendaftaran: {{ registration_number }}\n\nLihat status pendaftaran: {{ login_url }}\n\nSalam,\nPanitia Hari Santri 2026"),
    "delegate_package_selected": ("Paket peserta dipilih: {{ package_name }}", "Assalamu’alaikum {{ participant_name }},\n\nAnda memilih {{ package_name }} ({{ package_code }}) untuk {{ event_name }}.\n\nHarga paket: {{ currency }} {{ amount }}\n\nLihat pesanan: {{ login_url }}\n\nSalam,\nPanitia Hari Santri 2026"),
    "exhibitor_package_selected": ("Paket tenant dipilih: {{ package_name }}", "Assalamu’alaikum {{ participant_name }},\n\nAnda memilih {{ package_name }} ({{ package_code }}) untuk {{ event_name }}.\n\nHarga paket: {{ currency }} {{ amount }}\n\nLihat pesanan: {{ login_url }}\n\nSalam,\nPanitia Hari Santri 2026"),
    "payment_confirmed": ("Pembayaran pesanan {{ order_number }} terkonfirmasi", "Assalamu’alaikum {{ participant_name }},\n\nPembayaran pesanan {{ order_number }} telah terkonfirmasi.\n\nJumlah: {{ currency }} {{ amount }}\nWaktu konfirmasi: {{ paid_at }}\n\nLihat pesanan: {{ login_url }}\n\nSalam,\nPanitia Hari Santri 2026"),
    "business_matching_profile_saved": ("Profil Anda berhasil disimpan", "Assalamu’alaikum {{ participant_name }},\n\nProfil Anda untuk {{ event_name }} berhasil disimpan.\n\nBuka dashboard: {{ login_url }}\n\nSalam,\nPanitia Hari Santri 2026"),
    "meeting_requested": ("Permintaan pertemuan baru", "Assalamu’alaikum {{ participant_name }},\n\n{{ counterparty_name }} mengundang Anda untuk bertemu di {{ event_name }}.\n\nTopik: {{ meeting_topic }}\n\nLihat permintaan: {{ login_url }}\n\nSalam,\nPanitia Hari Santri 2026"),
    "meeting_accepted": ("Permintaan pertemuan Anda diterima", "Assalamu’alaikum {{ participant_name }},\n\n{{ counterparty_name }} menerima permintaan pertemuan Anda.\n\nTopik: {{ meeting_topic }}\n\nLanjutkan penjadwalan: {{ login_url }}\n\nSalam,\nPanitia Hari Santri 2026"),
    "meeting_confirmed": ("Pertemuan Anda terjadwal", "Assalamu’alaikum {{ participant_name }},\n\nPertemuan Anda dengan {{ counterparty_name }} telah dikonfirmasi.\n\nTopik: {{ meeting_topic }}\nJadwal: {{ meeting_schedule }}\nLokasi: {{ meeting_venue }}\n\nLihat detail: {{ login_url }}\n\nSalam,\nPanitia Hari Santri 2026"),
    "meeting_declined": ("Permintaan pertemuan ditolak", "Assalamu’alaikum {{ participant_name }},\n\n{{ counterparty_name }} menolak permintaan pertemuan tentang {{ meeting_topic }}.\n\nBuka dashboard: {{ login_url }}\n\nSalam,\nPanitia Hari Santri 2026"),
    "meeting_cancelled": ("Pertemuan dibatalkan", "Assalamu’alaikum {{ participant_name }},\n\nPertemuan dengan {{ counterparty_name }} tentang {{ meeting_topic }} dibatalkan.\n\nLihat jadwal: {{ login_url }}\n\nSalam,\nPanitia Hari Santri 2026"),
    "meeting_reschedule_requested": ("Permintaan perubahan jadwal", "Assalamu’alaikum {{ participant_name }},\n\n{{ counterparty_name }} meminta perubahan jadwal pertemuan tentang {{ meeting_topic }}.\n\nLihat permintaan: {{ login_url }}\n\nSalam,\nPanitia Hari Santri 2026"),
}

DEFAULT_TEMPLATES_BY_LOCALE = {"id": ID_DEFAULT_TEMPLATES, "en": DEFAULT_TEMPLATES}


def render(template: str, variables: dict[str, object]) -> str:
    return TOKEN_PATTERN.sub(lambda match: str(variables.get(match.group(1), "")), template)


def apply_content_translations(
    values: dict[str, object],
    *,
    event_translation=None,
    product_translation=None,
    package_translation=None,
    rate_translation=None,
    resource_translation=None,
) -> dict[str, object]:
    localized = dict(values)
    if event_translation and "name" in event_translation.fields:
        localized["event_name"] = event_translation.fields["name"]
    if product_translation and "name" in product_translation.fields:
        localized["package_name"] = product_translation.fields["name"]
    if package_translation or rate_translation:
        package_name = package_translation.fields.get("name") if package_translation else None
        rate_name = rate_translation.fields.get("name") if rate_translation else None
        if package_name and rate_name:
            localized["package_name"] = f"{package_name} - {rate_name}"
        elif package_name:
            localized["package_name"] = package_name
        elif rate_name and localized.get("package_name"):
            source_package = str(localized["package_name"]).split(" - ", 1)[0]
            localized["package_name"] = f"{source_package} - {rate_name}"
    if resource_translation and "name" in resource_translation.fields:
        localized["meeting_venue"] = resource_translation.fields["name"]
    return localized


async def ensure_event_templates(db, event_id: UUID, locale: str | None = None) -> list[EmailNotificationTemplate]:
    wanted_locales = [normalize_locale(locale)] if locale else list(SUPPORTED_LOCALES)
    existing = {(row.trigger, row.locale): row for row in (await db.execute(select(EmailNotificationTemplate).where(EmailNotificationTemplate.event_id == event_id))).scalars()}
    for wanted_locale in wanted_locales:
        for trigger, (subject, body) in DEFAULT_TEMPLATES_BY_LOCALE[wanted_locale].items():
            if (trigger, wanted_locale) not in existing:
                row = EmailNotificationTemplate(event_id=event_id, trigger=trigger, locale=wanted_locale, subject_template=subject, body_template=body, available_variables=TRIGGER_VARIABLES[trigger])
                db.add(row); existing[(trigger, wanted_locale)] = row
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
    stmt = select(EmailNotificationTemplate).where(EmailNotificationTemplate.event_id == event_id)
    if locale:
        stmt = stmt.where(EmailNotificationTemplate.locale == normalize_locale(locale))
    rows = (await db.execute(stmt)).scalars().all()
    return sorted(rows, key=lambda row: (row.locale, row.trigger))


async def select_delivery_template(db, event_id: UUID, trigger: str, requested_locale: str):
    requested_locale = normalize_locale(requested_locale)
    locales = [requested_locale] if requested_locale == "en" else [requested_locale, "en"]
    rows = (await db.execute(select(EmailNotificationTemplate).where(
        EmailNotificationTemplate.event_id == event_id,
        EmailNotificationTemplate.trigger == trigger,
        EmailNotificationTemplate.locale.in_(locales),
    ))).scalars().all()
    by_locale = {row.locale: row for row in rows}
    requested = by_locale.get(requested_locale)
    if requested is not None:
        # Disabled is an explicit organizer decision and must not be bypassed.
        return requested if requested.is_enabled else None
    fallback = by_locale.get("en")
    return fallback if fallback and fallback.is_enabled else None


async def deliver(event_id: UUID, trigger: str, recipient: str, variables: dict[str, object], entity_type: str | None = None, entity_id: UUID | None = None, locale: str = DEFAULT_LOCALE) -> bool:
    settings = get_settings()
    if not settings.EMAIL_ENABLED:
        return False
    async with AsyncSessionFactory() as db:
        locale = normalize_locale(locale)
        await ensure_event_templates(db, event_id, locale)
        template = await select_delivery_template(db, event_id, trigger, locale)
        if not template:
            return False
        if entity_id and entity_type != "test":
            already_sent = (await db.execute(select(EmailNotificationLog.id).where(EmailNotificationLog.event_id == event_id, EmailNotificationLog.trigger == trigger, EmailNotificationLog.recipient == recipient, EmailNotificationLog.entity_type == entity_type, EmailNotificationLog.entity_id == entity_id, EmailNotificationLog.status == "sent").limit(1))).scalar_one_or_none()
            if already_sent:
                return True
        subject, body = render(template.subject_template, variables), render(template.body_template, variables)
        log = EmailNotificationLog(event_id=event_id, template_id=template.id, trigger=trigger, locale=template.locale, recipient=recipient, subject=subject, entity_type=entity_type, entity_id=entity_id)
        db.add(log); await db.commit(); await db.refresh(log)
        message = EmailMessage(); message["Subject"] = subject; message["From"] = f"{settings.EMAIL_FROM_NAME} <{settings.EMAIL_FROM_ADDRESS}>"; message["To"] = recipient; message.set_content(body)
        try:
            await asyncio.to_thread(_send_message, message, settings.EMAIL_SMTP_HOST, settings.EMAIL_SMTP_PORT, settings.EMAIL_SMTP_USERNAME, settings.EMAIL_SMTP_PASSWORD, settings.EMAIL_SMTP_USE_SSL, settings.EMAIL_SMTP_USE_TLS)
            log.status = "sent"; log.sent_at = datetime.now(timezone.utc)
        except Exception as exc:
            logger.exception("Email notification failed; trigger=%s recipient=%s", trigger, recipient)
            log.status = "failed"; log.error_message = str(exc)[:2000]
        await db.commit()
        return log.status == "sent"


async def deliver_to_user(event_id: UUID, trigger: str, user_id: UUID, variables: dict[str, object], entity_type: str | None = None, entity_id: UUID | None = None) -> bool:
    async with AsyncSessionFactory() as db:
        user = await db.get(User, user_id)
        preference = (await db.execute(select(EmailNotificationPreference).where(
            EmailNotificationPreference.event_id == event_id,
            EmailNotificationPreference.user_id == user_id,
            EmailNotificationPreference.trigger == trigger,
        ))).scalar_one_or_none()
        from app.modules.content_translations.service import translation_map
        event_translation = (await translation_map(db, "event", [event_id], user.preferred_locale)).get(event_id) if user else None
        localized_variables = dict(variables)
        product_id = localized_variables.pop("_product_id", None)
        package_id = localized_variables.pop("_delegate_package_id", None)
        rate_id = localized_variables.pop("_delegate_package_rate_id", None)
        resource_id = localized_variables.pop("_meeting_resource_id", None)
        product_translation = (await translation_map(db, "product", [product_id], user.preferred_locale)).get(product_id) if user and product_id else None
        package_translation = (await translation_map(db, "delegate_package", [package_id], user.preferred_locale)).get(package_id) if user and package_id else None
        rate_translation = (await translation_map(db, "delegate_package_rate", [rate_id], user.preferred_locale)).get(rate_id) if user and rate_id else None
        resource_translation = (await translation_map(db, "meeting_resource", [resource_id], user.preferred_locale)).get(resource_id) if user and resource_id else None
    if not user:
        return False
    if preference is not None and not preference.is_enabled:
        logger.info("Email notification disabled for account; trigger=%s user_id=%s", trigger, user_id)
        return False
    values = apply_content_translations(
        {"participant_name": user.full_name or user.email, "login_url": frontend_login_url(), **localized_variables},
        event_translation=event_translation,
        product_translation=product_translation,
        package_translation=package_translation,
        rate_translation=rate_translation,
        resource_translation=resource_translation,
    )
    return await deliver(event_id, trigger, user.email, values, entity_type, entity_id, user.preferred_locale)


async def deliver_account_registration(user_id: UUID) -> bool:
    from app.core.email import send_registration_confirmation
    from app.modules.events.models import Event

    async with AsyncSessionFactory() as db:
        user = await db.get(User, user_id)
        event = (await db.execute(select(Event).order_by(Event.start_at.desc()).limit(1))).scalar_one_or_none()
    if not user:
        return False
    if not event:
        await send_registration_confirmation(user.email)
        return True
    return await deliver_to_user(event.id, "account_registered", user.id, {"event_name": event.name}, "user", user.id)


async def deliver_payment_for_order(order_id: UUID) -> bool:
    from app.modules.events.models import Event
    from app.modules.payments.models import Order, Payment
    from app.modules.registrations.models import Registration
    from app.modules.store.models import OrderItem, Product

    async with AsyncSessionFactory() as db:
        order = await db.get(Order, order_id)
        if not order:
            return False
        event_id = None
        if order.registration_id:
            registration = await db.get(Registration, order.registration_id)
            event_id = registration.event_id if registration else None
        if event_id is None:
            event_id = (await db.execute(select(Product.event_id).join(OrderItem, OrderItem.product_id == Product.id).where(OrderItem.order_id == order.id).limit(1))).scalar_one_or_none()
        event = await db.get(Event, event_id) if event_id else None
        payment = (await db.execute(select(Payment).where(Payment.order_id == order.id).order_by(Payment.created_at.desc()).limit(1))).scalar_one_or_none()
        user_id = order.user_id
    if not event:
        return False
    paid_at = payment.paid_at if payment else None
    return await deliver_to_user(event.id, "payment_confirmed", user_id, {"event_name": event.name, "order_number": order.order_number, "amount": order.total_amount, "currency": order.currency, "paid_at": paid_at.isoformat() if paid_at else ""}, "order", order.id)


async def deliver_meeting_update(meeting_id: UUID, trigger: str, target_user_id: UUID, actor_participant_id: UUID) -> bool:
    from app.modules.business_matching.models import Meeting, MeetingResource, MeetingSlot
    from app.modules.events.models import Event
    from app.modules.participants.models import ParticipantProfile

    async with AsyncSessionFactory() as db:
        meeting = await db.get(Meeting, meeting_id)
        if not meeting:
            return False
        event = await db.get(Event, meeting.event_id)
        actor = await db.get(ParticipantProfile, actor_participant_id)
        slot = await db.get(MeetingSlot, meeting.confirmed_slot_id) if meeting.confirmed_slot_id else None
        resource = await db.get(MeetingResource, meeting.venue_resource_id) if meeting.venue_resource_id else None
    values = {
        "event_name": event.name if event else "",
        "counterparty_name": actor.full_name if actor else "Participant",
        "meeting_topic": meeting.topic,
        "meeting_schedule": f"{slot.starts_at.isoformat()} - {slot.ends_at.isoformat()}" if slot else "",
        "meeting_venue": resource.name if resource else "",
        "_meeting_resource_id": resource.id if resource else None,
    }
    return await deliver_to_user(meeting.event_id, trigger, target_user_id, values, "meeting", meeting.id)
