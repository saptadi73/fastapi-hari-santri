from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user, get_db_session, require_admin
from app.modules.hari_santri import schemas
from app.modules.hari_santri.models import ShirtSize
from app.modules.hari_santri.service import HariSantriService
from app.modules.events.models import Event
from app.modules.users.models import User
from app.support.responses import success_response

router = APIRouter(tags=["hari-santri-2026"])


def _shirt_size_read(size: ShirtSize, inventory):
    return schemas.ShirtSizeRead(
        id=size.id,
        event_id=size.event_id,
        code=size.code,
        label=size.label,
        size_chart=size.size_chart,
        active=size.active,
        sort_order=size.sort_order,
        capacity=inventory.capacity,
        reserved=inventory.reserved,
        allocated=inventory.allocated,
        available=inventory.capacity - inventory.reserved - inventory.allocated,
    )


@router.get("/events/{event_id}/shirt-sizes")
async def list_shirt_sizes(event_id: UUID, request: Request, db: AsyncSession = Depends(get_db_session)):
    rows = await HariSantriService.list_shirt_sizes(db, event_id)
    data = [_shirt_size_read(size, inventory) for size, inventory in rows]
    return success_response("Ukuran kaos tersedia", data=data, request=request)


@router.get("/admin/events/{event_id}/shirt-sizes")
async def admin_list_shirt_sizes(
    event_id: UUID,
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
):
    rows = await HariSantriService.list_shirt_sizes(db, event_id, active_only=False)
    data = [_shirt_size_read(size, inventory) for size, inventory in rows]
    return success_response("Inventori ukuran kaos ditemukan", data=data, request=request)


@router.post("/admin/events/{event_id}/shirt-sizes", status_code=201)
async def create_shirt_size(
    event_id: UUID,
    payload: schemas.ShirtSizeWrite,
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
):
    size, inventory = await HariSantriService.create_shirt_size(db, event_id, payload)
    return success_response("Ukuran kaos dibuat", data=_shirt_size_read(size, inventory), request=request)


@router.put("/admin/shirt-sizes/{size_id}")
async def update_shirt_size(
    size_id: UUID,
    payload: schemas.ShirtSizeWrite,
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
):
    size, inventory = await HariSantriService.update_shirt_size(db, size_id, payload)
    return success_response("Ukuran dan stok kaos diperbarui", data=_shirt_size_read(size, inventory), request=request)


@router.get("/orders/{order_id}/participants")
async def get_order_participants(
    order_id: UUID,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    rows = await HariSantriService.get_order_participants(db, order_id, user.id)
    data = [schemas.OrderParticipantRead(
        id=row.id,
        participant_number=row.participant_number,
        full_name=row.full_name,
        birth_date=row.birth_date,
        guardian_name=row.guardian_name,
        guardian_contact=row.guardian_contact,
        activity_type=row.activity_type,
        shirt_size_code=row.shirt_size_code_snapshot,
        status=row.status,
        created_at=row.created_at,
    ) for row in rows]
    return success_response("Peserta order ditemukan", data=data, request=request)


@router.put("/orders/{order_id}/participants")
async def replace_order_participants(
    order_id: UUID,
    payload: schemas.OrderParticipantsWrite,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    rows = await HariSantriService.replace_order_participants(db, order_id, user.id, payload.participants)
    data = [schemas.OrderParticipantRead(
        id=row.id,
        participant_number=row.participant_number,
        full_name=row.full_name,
        birth_date=row.birth_date,
        guardian_name=row.guardian_name,
        guardian_contact=row.guardian_contact,
        activity_type=row.activity_type,
        shirt_size_code=row.shirt_size_code_snapshot,
        status=row.status,
        created_at=row.created_at,
    ) for row in rows]
    return success_response("Peserta order dan reservasi kaos disimpan", data=data, request=request)


@router.post("/hari-santri/orders/{order_id}/checkout")
async def create_payment_portal_checkout(
    order_id: UUID,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    data = await HariSantriService.create_checkout(db, order_id, user.id)
    return success_response("Checkout Payment Portal siap", data=data, request=request)


@router.get("/hari-santri/orders/{order_id}/payment-status")
async def get_payment_portal_status(
    order_id: UUID,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    data = await HariSantriService.payment_status(db, order_id, user.id)
    return success_response("Status pembayaran order ditemukan", data=data, request=request)


@router.post("/integrations/payment-portal/callback")
async def payment_portal_callback(request: Request, db: AsyncSession = Depends(get_db_session)):
    data = await HariSantriService.process_payment_callback(
        db,
        await request.body(),
        request.headers.get("X-Event-ID", ""),
        request.headers.get("X-Timestamp", ""),
        request.headers.get("X-Signature", ""),
    )
    return success_response("Callback Payment Portal diproses", data=data, request=request)


@router.get("/hari-santri/me/tickets")
async def get_my_hari_santri_tickets(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    rows = await HariSantriService.get_user_tickets(db, user.id)
    data = [schemas.HariSantriTicketRead(**row) for row in rows]
    return success_response("Tiket Hari Santri ditemukan", data=data, request=request)


@router.post("/hari-santri/staff/checkins")
async def checkin_hari_santri_ticket(
    payload: schemas.HariSantriCheckinWrite,
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
):
    data = await HariSantriService.checkin_ticket(db, payload.qr_token, admin.id)
    return success_response("Check-in Hari Santri berhasil", data=data, request=request)


@router.post("/bazaar/applications", status_code=201)
async def create_bazaar_application(
    payload: schemas.BazaarApplicationWrite,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    event_id = (await db.execute(
        select(Event.id).where(Event.slug == HariSantriService.EVENT_SLUG).limit(1)
    )).scalar_one_or_none()
    if not event_id:
        from app.core.exceptions import NotFoundException
        raise NotFoundException("HARI_SANTRI_EVENT_NOT_FOUND", "Event Hari Santri tidak ditemukan")
    row = await HariSantriService.create_bazaar_application(db, event_id, user.id, payload)
    return success_response("Pengajuan tenant diterima", data=schemas.BazaarApplicationRead.model_validate(row), request=request)


@router.get("/bazaar/me/applications")
async def list_my_bazaar_applications(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    rows = await HariSantriService.list_my_bazaar_applications(db, user.id)
    data = [schemas.BazaarApplicationRead.model_validate(row) for row in rows]
    return success_response("Pengajuan tenant ditemukan", data=data, request=request)


@router.get("/admin/events/{event_id}/bazaar/applications")
async def list_bazaar_applications(
    event_id: UUID,
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
):
    rows = await HariSantriService.list_bazaar_applications(db, event_id)
    data = [schemas.BazaarApplicationRead.model_validate(row) for row in rows]
    return success_response("Daftar pengajuan tenant ditemukan", data=data, request=request)


@router.patch("/admin/bazaar/applications/{application_id}")
async def decide_bazaar_application(
    application_id: UUID,
    payload: schemas.BazaarApplicationDecision,
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
):
    row = await HariSantriService.decide_bazaar_application(db, application_id, payload)
    return success_response("Status pengajuan tenant diperbarui", data=schemas.BazaarApplicationRead.model_validate(row), request=request)