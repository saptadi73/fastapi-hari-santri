from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db_session, require_admin
from app.core.exceptions import NotFoundException
from app.modules.content_translations import schemas
from app.modules.content_translations.models import ContentTranslation
from app.modules.content_translations.service import TRANSLATABLE_FIELDS, ensure_entity, upsert, validate_entity_type, validate_locale
from app.modules.users.models import User
from app.support.responses import success_response

router = APIRouter(prefix="/admin/content-translations", tags=["admin-content-translations"])


@router.get("/entities")
async def entities(request: Request, admin: User = Depends(require_admin)):
    return success_response("Translatable entities found", {key: sorted(value) for key, value in TRANSLATABLE_FIELDS.items()}, request=request)


@router.get("/{entity_type}/{entity_id}")
async def list_entity_translations(entity_type: str, entity_id: UUID, request: Request, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db_session)):
    validate_entity_type(entity_type)
    await ensure_entity(db, entity_type, entity_id)
    rows = (await db.execute(select(ContentTranslation).where(ContentTranslation.entity_type == entity_type, ContentTranslation.entity_id == entity_id, ContentTranslation.locale == "id").order_by(ContentTranslation.locale))).scalars().all()
    return success_response("Content translations found", [schemas.TranslationRead.model_validate(row) for row in rows], request=request)


@router.put("/{entity_type}/{entity_id}/{locale}")
async def put_translation(entity_type: str, entity_id: UUID, locale: str, payload: schemas.TranslationWrite, request: Request, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db_session)):
    row = await upsert(db, entity_type, entity_id, locale, payload.fields, admin.id)
    return success_response("Content translation updated", schemas.TranslationRead.model_validate(row), request=request)


@router.delete("/{entity_type}/{entity_id}/{locale}")
async def delete_translation(entity_type: str, entity_id: UUID, locale: str, request: Request, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db_session)):
    validate_entity_type(entity_type)
    locale = validate_locale(locale)
    await ensure_entity(db, entity_type, entity_id)
    row = (await db.execute(select(ContentTranslation).where(ContentTranslation.entity_type == entity_type, ContentTranslation.entity_id == entity_id, ContentTranslation.locale == locale))).scalar_one_or_none()
    if not row:
        raise NotFoundException("CONTENT_TRANSLATION_NOT_FOUND", "Content translation was not found")
    await db.delete(row)
    await db.commit()
    return success_response("Content translation deleted", {"entity_type": entity_type, "entity_id": entity_id, "locale": locale}, request=request)
