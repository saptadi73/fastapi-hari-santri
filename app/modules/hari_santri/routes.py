from uuid import UUID
import hashlib
import logging

from datetime import date, datetime
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user, get_db_session, require_admin, require_checkin_staff
from app.core.exceptions import AppException
from app.core.rate_limit import enforce_voucher_scan_rate_limit, voucher_scan_rate_limit_key
from app.modules.hari_santri import schemas
from app.modules.hari_santri.models import ShirtSize
from app.modules.hari_santri.payment_portal import PaymentPortalClient
from app.modules.hari_santri.service import HariSantriService
from app.modules.events.models import Event
from app.modules.users.models import User
from app.support.responses import success_response

router = APIRouter(tags=["hari-santri-2026"])
logger = logging.getLogger(__name__)


async def _require_voucher_scan_rate_limit(request: Request, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db_session)) -> User:
    await enforce_voucher_scan_rate_limit(
        db,
        voucher_scan_rate_limit_key(
            user_id=user.id,
            client_host=request.client.host if request.client else None,
        )
    )
    return user


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
        province_code=row.province_code,
        regency_code=row.regency_code,
        district_code=row.district_code,
        village_code=row.village_code,
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
        province_code=row.province_code,
        regency_code=row.regency_code,
        district_code=row.district_code,
        village_code=row.village_code,
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


@router.post("/admin/hari-santri/payments/{reference_id}/reconcile")
async def reconcile_payment_portal_order(
    reference_id: str,
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
):
    request_id = str(getattr(request.state, "request_id", ""))
    data = await HariSantriService.reconcile_payment(db, reference_id, admin.id, request_id=request_id)
    return success_response("Rekonsiliasi Payment Portal selesai", data=data, request=request)


@router.post("/integrations/payment-portal/callback")
async def payment_portal_callback(request: Request, db: AsyncSession = Depends(get_db_session)):
    raw_body = await request.body()
    event_id = request.headers.get("X-Event-ID", "")
    request_id = str(getattr(request.state, "request_id", ""))
    payload = PaymentPortalClient.verify_callback(
        raw_body,
        event_id,
        request.headers.get("X-Timestamp", ""),
        request.headers.get("X-Signature", ""),
    )
    payload_hash = hashlib.sha256(raw_body).hexdigest()
    try:
        data = await HariSantriService.process_verified_payment_callback(
            db, payload, event_id, payload_hash, request_id=request_id
        )
    except Exception as exc:
        await db.rollback()
        error_code = exc.code if isinstance(exc, AppException) else type(exc).__name__
        outbox = await HariSantriService.enqueue_failed_callback(
            db,
            payload=payload,
            event_id=event_id,
            payload_hash=payload_hash,
            request_id=request_id,
            error_code=str(error_code),
            retryable=not isinstance(exc, AppException),
        )
        logger.error(
            "Verified Hari Santri callback processing failed",
            extra={"request_id": request_id, "callback_event_id": event_id, "outbox_id": str(outbox.id), "error_code": str(error_code), "outbox_status": outbox.status},
        )
        data = {"queued_for_retry": outbox.status == "pending", "outbox_status": outbox.status, "outbox_id": str(outbox.id)}
    message = "Callback Payment Portal dijadwalkan untuk retry" if data.get("queued_for_retry") else "Callback Payment Portal diproses"
    return success_response(message, data=data, request=request)


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
    admin: User = Depends(require_checkin_staff),
    db: AsyncSession = Depends(get_db_session),
):
    try:
        data = await HariSantriService.checkin_ticket(db, payload.qr_token, admin.id, request_id=str(getattr(request.state, "request_id", "")))
    except AppException as exc:
        await db.rollback()
        await HariSantriService.record_audit(db, admin.id, "checkin_rejected", "hari_santri_ticket", None, {"request_id": str(getattr(request.state, "request_id", "")), "error_code": exc.code})
        raise
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


@router.post("/admin/hari-santri/vouchers", status_code=201)
async def create_participant_voucher(payload: schemas.VoucherCreate, request: Request, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db_session)):
    data = await HariSantriService.create_voucher(db, payload.participant_id, payload.initial_balance, admin.id)
    return success_response("Kartu voucher peserta dibuat", data=schemas.VoucherRead(**data), request=request)


@router.get("/admin/hari-santri/voucher-participants")
async def list_voucher_participants(request: Request, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db_session)):
    data = await HariSantriService.list_voucher_participants(db)
    return success_response("Daftar peserta berbayar tanpa voucher ditemukan", data=[schemas.VoucherParticipantRead(**item) for item in data], request=request)


@router.get("/admin/hari-santri/vouchers")
async def list_participant_vouchers(request: Request, status: str | None = Query(default=None, pattern="^(active|revoked)$"), admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db_session)):
    data = await HariSantriService.list_vouchers(db, status)
    return success_response("Daftar kartu voucher ditemukan", data=[schemas.VoucherRead(**item) for item in data], request=request)


@router.post("/admin/hari-santri/vouchers/{voucher_id}/revoke")
async def revoke_participant_voucher(voucher_id: UUID, request: Request, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db_session)):
    data = await HariSantriService.revoke_voucher(db, voucher_id, admin.id)
    return success_response("Kartu voucher dinonaktifkan", data=data, request=request)


@router.post("/admin/hari-santri/vouchers/{voucher_id}/adjust")
async def adjust_participant_voucher(voucher_id: UUID, payload: schemas.WalletAdjustmentWrite, request: Request, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db_session)):
    data = await HariSantriService.adjust_voucher(db, voucher_id, payload.amount, payload.reason, admin.id)
    return success_response("Saldo voucher disesuaikan", data=data, request=request)


@router.get("/hari-santri/me/wallet")
async def get_my_wallet(request: Request, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db_session)):
    data = await HariSantriService.get_my_wallet(db, user.id)
    return success_response("Saldo voucher peserta ditemukan", data=schemas.VoucherRead(**data), request=request)


@router.get("/hari-santri/me/wallets")
async def list_my_wallets(request: Request, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db_session)):
    data = await HariSantriService.list_my_wallets(db, user.id)
    return success_response("Daftar saldo voucher peserta ditemukan", data=[schemas.VoucherRead(**item) for item in data], request=request)


@router.get("/hari-santri/me/wallet/transfers")
async def list_my_wallet_transfers(request: Request, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db_session)):
    data = await HariSantriService.list_participant_transfers(db, user.id)
    return success_response("Riwayat pemakaian voucher ditemukan", data=[schemas.WalletTransferHistoryRead(**item) for item in data], request=request)


@router.post("/hari-santri/exhibitors/{exhibitor_id}/wallet/charge")
async def charge_exhibitor_wallet(exhibitor_id: UUID, payload: schemas.VoucherScan, request: Request, user: User = Depends(_require_voucher_scan_rate_limit), db: AsyncSession = Depends(get_db_session)):
    try:
        data = await HariSantriService.transfer_voucher(db, payload.qr_token, payload.amount, payload.request_id, payload.participant_password, exhibitor_id, user.id, is_admin=user.role in {"admin", "organizer"})
    except AppException as exc:
        await db.rollback()
        await HariSantriService.record_audit(db, user.id, "voucher_scan_failed", "wallet_transfer", None, {"exhibitor_id": str(exhibitor_id), "amount": payload.amount, "request_id": payload.request_id, "error_code": exc.code})
        raise
    return success_response("Saldo voucher berhasil dipindahkan ke exhibitor", data=schemas.WalletTransferRead(**data), request=request)


@router.post("/hari-santri/me/exhibitor/wallet/charge")
async def charge_my_exhibitor_wallet(payload: schemas.VoucherScan, request: Request, user: User = Depends(_require_voucher_scan_rate_limit), db: AsyncSession = Depends(get_db_session)):
    exhibitor = await HariSantriService.get_my_exhibitor(db, user.id)
    try:
        data = await HariSantriService.transfer_voucher(db, payload.qr_token, payload.amount, payload.request_id, payload.participant_password, exhibitor.id, user.id)
    except AppException as exc:
        await db.rollback()
        await HariSantriService.record_audit(db, user.id, "voucher_scan_failed", "wallet_transfer", None, {"exhibitor_id": str(exhibitor.id), "amount": payload.amount, "request_id": payload.request_id, "error_code": exc.code})
        raise
    return success_response("Saldo voucher berhasil dipindahkan ke exhibitor", data=schemas.WalletTransferRead(**data), request=request)


@router.get("/hari-santri/me/exhibitors")
async def list_my_exhibitors(request: Request, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db_session)):
    data = await HariSantriService.list_my_exhibitors(db, user.id)
    return success_response("Daftar lapak exhibitor akun ditemukan", data=[schemas.MyExhibitorRead(**item) for item in data], request=request)


@router.post("/hari-santri/me/exhibitors/{exhibitor_id}/wallet/charge")
async def charge_selected_exhibitor_wallet(exhibitor_id: UUID, payload: schemas.VoucherScan, request: Request, user: User = Depends(_require_voucher_scan_rate_limit), db: AsyncSession = Depends(get_db_session)):
    exhibitor = await HariSantriService.get_my_exhibitor(db, user.id, exhibitor_id)
    try:
        data = await HariSantriService.transfer_voucher(db, payload.qr_token, payload.amount, payload.request_id, payload.participant_password, exhibitor.id, user.id)
    except AppException as exc:
        await db.rollback()
        await HariSantriService.record_audit(db, user.id, "voucher_scan_failed", "wallet_transfer", None, {"exhibitor_id": str(exhibitor.id), "amount": payload.amount, "request_id": payload.request_id, "error_code": exc.code})
        raise
    return success_response("Saldo voucher berhasil dipindahkan ke exhibitor", data=schemas.WalletTransferRead(**data), request=request)


@router.get("/hari-santri/me/exhibitors/{exhibitor_id}/wallet")
async def get_selected_exhibitor_wallet(exhibitor_id: UUID, request: Request, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db_session)):
    data = await HariSantriService.get_exhibitor_wallet(db, exhibitor_id, user.id)
    return success_response("Saldo exhibitor ditemukan", data=schemas.WalletRead(**data), request=request)


@router.get("/hari-santri/me/exhibitors/{exhibitor_id}/wallet/transfers")
async def list_selected_exhibitor_wallet_transfers(exhibitor_id: UUID, request: Request, date_from: date | None = Query(default=None), date_to: date | None = Query(default=None), user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db_session)):
    data = await HariSantriService.list_wallet_transfers(db, exhibitor_id, user.id, date_from=date_from, date_to=date_to)
    return success_response("Riwayat saldo exhibitor ditemukan", data=[schemas.WalletTransferHistoryRead(**item) for item in data], request=request)


@router.get("/hari-santri/me/exhibitor/wallet")
async def get_my_exhibitor_wallet(request: Request, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db_session)):
    exhibitor = await HariSantriService.get_my_exhibitor(db, user.id)
    data = await HariSantriService.get_exhibitor_wallet(db, exhibitor.id, user.id)
    return success_response("Saldo exhibitor ditemukan", data=schemas.WalletRead(**data), request=request)


@router.get("/hari-santri/me/exhibitor/wallet/transfers")
async def list_my_exhibitor_wallet_transfers(request: Request, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db_session)):
    exhibitor = await HariSantriService.get_my_exhibitor(db, user.id)
    data = await HariSantriService.list_wallet_transfers(db, exhibitor.id, user.id)
    return success_response("Riwayat saldo exhibitor ditemukan", data=[schemas.WalletTransferHistoryRead(**item) for item in data], request=request)


@router.get("/hari-santri/exhibitors/{exhibitor_id}/wallet")
async def get_exhibitor_wallet(exhibitor_id: UUID, request: Request, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db_session)):
    data = await HariSantriService.get_exhibitor_wallet(db, exhibitor_id, user.id, is_admin=user.role in {"admin", "organizer"})
    return success_response("Saldo exhibitor ditemukan", data=schemas.WalletRead(**data), request=request)


@router.get("/hari-santri/exhibitors/{exhibitor_id}/wallet/transfers")
async def list_exhibitor_wallet_transfers(exhibitor_id: UUID, request: Request, date_from: date | None = Query(default=None), date_to: date | None = Query(default=None), user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db_session)):
    data = await HariSantriService.list_wallet_transfers(db, exhibitor_id, user.id, is_admin=user.role in {"admin", "organizer"}, date_from=date_from, date_to=date_to)
    return success_response("Riwayat saldo exhibitor ditemukan", data=[schemas.WalletTransferHistoryRead(**item) for item in data], request=request)


@router.get("/hari-santri/exhibitors/{exhibitor_id}/wallet/transfers.csv")
async def export_exhibitor_wallet_transfers(exhibitor_id: UUID, date_from: date | None = Query(default=None), date_to: date | None = Query(default=None), user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db_session)):
    data = await HariSantriService.list_wallet_transfers(db, exhibitor_id, user.id, is_admin=user.role in {"admin", "organizer"}, date_from=date_from, date_to=date_to)
    filename = f"exhibitor-wallet-{exhibitor_id}-{datetime.now().date().isoformat()}.csv"
    return Response(HariSantriService.wallet_transfers_csv(data), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/admin/hari-santri/exhibitors/approved")
async def list_approved_exhibitors(request: Request, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db_session)):
    data = await HariSantriService.list_approved_exhibitors(db)
    return success_response("Daftar exhibitor disetujui ditemukan", data=[schemas.ExhibitorWalletTargetRead(**item) for item in data], request=request)


@router.get("/admin/hari-santri/settlements")
async def list_exhibitor_settlements(request: Request, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db_session)):
    data = await HariSantriService.list_settlements(db)
    return success_response("Daftar settlement ditemukan", data=[schemas.SettlementAdminRead(**item) for item in data], request=request)


@router.get("/admin/hari-santri/settlements/reconciliation")
async def settlement_reconciliation(request: Request, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db_session)):
    data = await HariSantriService.list_settlement_reconciliation(db)
    return success_response("Rekonsiliasi settlement ditemukan", data=[schemas.SettlementReconciliationRead(**item) for item in data], request=request)


@router.get("/admin/hari-santri/settlements/reconciliation.csv")
async def settlement_reconciliation_csv(admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db_session)):
    data = await HariSantriService.list_settlement_reconciliation(db)
    filename = f"hari-santri-settlement-reconciliation-{datetime.now().date().isoformat()}.csv"
    return Response(HariSantriService.settlement_reconciliation_csv(data), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.post("/hari-santri/me/exhibitors/{exhibitor_id}/settlements", status_code=201)
async def request_my_exhibitor_settlement(exhibitor_id: UUID, payload: schemas.SettlementRequest, request: Request, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db_session)):
    exhibitor = await HariSantriService.get_my_exhibitor(db, user.id, exhibitor_id)
    data = await HariSantriService.create_full_settlement(db, exhibitor.id, payload.payment_reference, payload.notes, user.id)
    return success_response("Settlement seluruh saldo tersedia dibuat", data=schemas.SettlementRead(**data), request=request)


@router.post("/hari-santri/me/exhibitor/settlements/{settlement_id}/confirm")
async def confirm_my_exhibitor_settlement(settlement_id: UUID, request: Request, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db_session)):
    data = await HariSantriService.confirm_settlement(db, settlement_id, user.id)
    return success_response("Pembayaran settlement dikonfirmasi oleh exhibitor", data=schemas.SettlementRead(**data), request=request)


@router.get("/admin/hari-santri/audit-logs")
async def list_hari_santri_audit_logs(request: Request, limit: int = Query(default=200, ge=1, le=500), action: str | None = Query(default=None, min_length=1, max_length=80), admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db_session)):
    data = await HariSantriService.list_audit_logs(db, limit, action)
    return success_response("Audit log Hari Santri ditemukan", data=[schemas.HariSantriAuditLogRead.model_validate(row) for row in data], request=request)


@router.get("/admin/hari-santri/operations/callback-outbox")
async def list_hari_santri_callback_outbox(request: Request, limit: int = Query(default=100, ge=1, le=500), status: str | None = Query(default=None, pattern="^(pending|processing|completed|dead)$"), admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db_session)):
    data = await HariSantriService.list_callback_outbox(db, limit=limit, status=status)
    return success_response("Callback outbox Hari Santri ditemukan", data=data, request=request)


@router.post("/admin/hari-santri/operations/callback-outbox/{outbox_id}/retry")
async def retry_hari_santri_callback_outbox(outbox_id: UUID, request: Request, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db_session)):
    data = await HariSantriService.retry_dead_callback_outbox(db, outbox_id, admin.id, str(getattr(request.state, "request_id", "")))
    return success_response("Callback dead-letter dijadwalkan ulang", data=data, request=request)

