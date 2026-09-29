from __future__ import annotations

from collections.abc import Iterable
from typing import Any
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundException, ValidationException
from app.core.i18n import normalize_locale
from app.modules.content_translations.models import ContentTranslation
from app.support.search import search_filter


TRANSLATABLE_FIELDS: dict[str, frozenset[str]] = {
    "event": frozenset({"name", "description", "venue_name", "venue_address"}),
    "product": frozenset({"name", "description"}),
    "delegate_package": frozenset({"name", "description"}),
    "delegate_package_rate": frozenset({"name"}),
    "delegate_package_facility": frozenset({"name", "description", "unit"}),
    "event_activity": frozenset({"name"}),
    "business_matching_slot": frozenset({"label"}),
    "session": frozenset({"title", "description", "session_type", "room_name"}),
    "speaker": frozenset({"professional_title", "organization_name", "biography", "expertise_tags", "session_title"}),
    "committee_member": frozenset({"role_title", "committee_group", "organization_name", "biography"}),
    "announcement": frozenset({"title", "body"}),
    "certificate": frozenset({"title"}),
    "matching_session": frozenset({"name"}),
    "meeting_venue": frozenset({"name", "location_description"}),
    "meeting_resource": frozenset({"name"}),
}


def translated_search_filter(search: str | None, entity_type: str, entity_id, *columns):
    """Search source fields and their translations across supported locales.

    EXISTS preserves one result per entity, including when multiple translations
    match. Only translated counterparts of the requested search fields are used.
    """
    if not (search or "").strip():
        return True
    translated_fields = [
        ContentTranslation.fields[column.key].as_string()
        for column in columns
        if column.key in TRANSLATABLE_FIELDS[entity_type]
    ]
    source = search_filter(search, *columns)
    if not translated_fields:
        return source
    translation = select(ContentTranslation.id).where(
        ContentTranslation.entity_type == entity_type,
        ContentTranslation.entity_id == entity_id,
        search_filter(search, *translated_fields),
    ).exists()
    return or_(source, translation)


def _model_for(entity_type: str):
    if entity_type == "event":
        from app.modules.events.models import Event
        return Event
    if entity_type == "product":
        from app.modules.store.models import Product
        return Product
    if entity_type in {"delegate_package", "delegate_package_rate", "delegate_package_facility", "event_activity", "business_matching_slot"}:
        from app.modules.iwbif import models
        return {
            "delegate_package": models.DelegatePackage,
            "delegate_package_rate": models.DelegatePackageRate,
            "delegate_package_facility": models.DelegatePackageFacility,
            "event_activity": models.EventActivity,
            "business_matching_slot": models.BusinessMatchingSlot,
        }[entity_type]
    if entity_type == "session":
        from app.modules.sessions.models import EventSession
        return EventSession
    if entity_type == "speaker":
        from app.modules.speakers.models import Speaker
        return Speaker
    if entity_type == "committee_member":
        from app.modules.committee.models import CommitteeMember
        return CommitteeMember
    if entity_type in {"announcement", "certificate"}:
        from app.modules.admin_content import models
        return {"announcement": models.Announcement, "certificate": models.Certificate}[entity_type]
    if entity_type in {"matching_session", "meeting_venue", "meeting_resource"}:
        from app.modules.business_matching import models
        return {
            "matching_session": models.MatchingSession,
            "meeting_venue": models.MeetingVenue,
            "meeting_resource": models.MeetingResource,
        }[entity_type]
    raise ValidationException("INVALID_TRANSLATION_ENTITY", "Translation entity type is not supported")


def validate_fields(entity_type: str, fields: dict[str, Any]) -> None:
    allowed = TRANSLATABLE_FIELDS.get(entity_type)
    if allowed is None:
        raise ValidationException("INVALID_TRANSLATION_ENTITY", "Translation entity type is not supported")
    invalid = sorted(set(fields) - allowed)
    if invalid:
        raise ValidationException("INVALID_TRANSLATION_FIELD", f"Fields are not translatable: {', '.join(invalid)}")
    for field, value in fields.items():
        if field == "expertise_tags":
            if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
                raise ValidationException("INVALID_TRANSLATION_VALUE", "expertise_tags must be a list of non-empty strings")
        elif not isinstance(value, str) or not value.strip():
            raise ValidationException("INVALID_TRANSLATION_VALUE", f"{field} must be a non-empty string")
        elif len(value) > 20_000:
            raise ValidationException("INVALID_TRANSLATION_VALUE", f"{field} exceeds the translation length limit")


def validate_entity_type(entity_type: str) -> str:
    if entity_type not in TRANSLATABLE_FIELDS:
        raise ValidationException("INVALID_TRANSLATION_ENTITY", "Translation entity type is not supported")
    return entity_type


def validate_locale(locale: str) -> str:
    normalized = locale.strip().replace("_", "-").lower()
    if normalized == "en" or normalized.startswith("en-"):
        return "en"
    if normalized == "id" or normalized.startswith(("id-", "in-")):
        return "id"
    raise ValidationException("UNSUPPORTED_LOCALE", "Supported locales are id and en")


async def ensure_entity(db: AsyncSession, entity_type: str, entity_id: UUID) -> None:
    if await db.get(_model_for(entity_type), entity_id) is None:
        raise NotFoundException("TRANSLATION_ENTITY_NOT_FOUND", "Translation target was not found")


async def upsert(db: AsyncSession, entity_type: str, entity_id: UUID, locale: str, fields: dict, user_id: UUID) -> ContentTranslation:
    validate_entity_type(entity_type)
    locale = validate_locale(locale)
    validate_fields(entity_type, fields)
    await ensure_entity(db, entity_type, entity_id)
    row = (await db.execute(select(ContentTranslation).where(
        ContentTranslation.entity_type == entity_type,
        ContentTranslation.entity_id == entity_id,
        ContentTranslation.locale == locale,
    ))).scalar_one_or_none()
    if row is None:
        row = ContentTranslation(entity_type=entity_type, entity_id=entity_id, locale=locale, fields=fields, created_by=user_id, updated_by=user_id)
        db.add(row)
    else:
        row.fields = fields
        row.updated_by = user_id
    await db.commit()
    await db.refresh(row)
    return row


async def translation_map(db: AsyncSession, entity_type: str, entity_ids: Iterable[UUID], locale: str) -> dict[UUID, ContentTranslation]:
    ids = list(dict.fromkeys(entity_ids))
    locale = normalize_locale(locale)
    if not ids:
        return {}
    locales = [locale] if locale == "en" else [locale, "en"]
    rows = (await db.execute(select(ContentTranslation).where(
        ContentTranslation.entity_type == entity_type,
        ContentTranslation.entity_id.in_(ids),
        ContentTranslation.locale.in_(locales),
    ))).scalars().all()
    result = {}
    for row in sorted(rows, key=lambda item: locales.index(item.locale), reverse=True):
        result[row.entity_id] = row
    return result


async def localize_models(db: AsyncSession, entity_type: str, rows: Iterable[Any], locale: str) -> list[dict[str, Any]]:
    source = list(rows)
    translations = await translation_map(db, entity_type, [row.id for row in source], locale)
    requested = normalize_locale(locale)
    result = []
    for row in source:
        if isinstance(row, BaseModel):
            data = row.model_dump(mode="json")
        else:
            data = {column.name: getattr(row, column.name) for column in row.__table__.columns}
        translation = translations.get(row.id)
        if translation:
            data.update(translation.fields)
        data["content_locale"] = translation.locale if translation else "source"
        data["translation_fallback"] = translation is None and requested != "en"
        result.append(data)
    return result
