from collections import Counter
import csv
import io
from datetime import date, datetime, timedelta, timezone
import base64
import hashlib
import hmac
from decimal import Decimal
from uuid import UUID

from sqlalchemy import and_, delete, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import AppException, ConflictException, NotFoundException, ValidationException
from app.core.security import verify_password
from app.modules.events.models import Event
from app.modules.hari_santri.models import BazaarApplication, ExhibitorSettlement, ExhibitorWallet, HariSantriAuditLog, HariSantriCallbackEvent, HariSantriCheckin, HariSantriPayment, HariSantriTicket, OrderParticipant, ParticipantVoucher, ParticipantWallet, ShirtInventory, ShirtSize, WalletAdjustment, WalletTransfer
from app.modules.hari_santri.payment_portal import PaymentPortalClient
from app.modules.hari_santri.schemas import OrderParticipantWrite
from app.modules.regions.service import RegionService
from app.modules.payments.models import Order, OrderStatus
from app.modules.store.models import OrderItem
from app.modules.users.models import User


class HariSantriService:
    EVENT_SLUG = "hari-santri-2026"
    VOUCHER_EXPIRES_AT = datetime(2026, 11, 15, 23, 59, 59, tzinfo=timezone(timedelta(hours=7)))
    EDITABLE_ORDER_STATUSES = {OrderStatus.DRAFT, OrderStatus.PENDING}

    @staticmethod
    def _audit(db: AsyncSession, actor_user_id: UUID | None, action: str, entity_type: str, entity_id: UUID | None, payload: dict | None = None) -> None:
        db.add(HariSantriAuditLog(actor_user_id=actor_user_id, action=action, entity_type=entity_type, entity_id=entity_id, payload=payload or {}))

    @staticmethod
    async def record_audit(db: AsyncSession, actor_user_id: UUID | None, action: str, entity_type: str, entity_id: UUID | None, payload: dict | None = None) -> None:
        HariSantriService._audit(db, actor_user_id, action, entity_type, entity_id, payload)
        await db.commit()

    @staticmethod
    async def list_shirt_sizes(db: AsyncSession, event_id: UUID, *, active_only: bool = True):
        query = (
            select(ShirtSize, ShirtInventory)
            .join(ShirtInventory, ShirtInventory.size_id == ShirtSize.id)
            .where(ShirtSize.event_id == event_id, ShirtInventory.event_id == event_id)
            .order_by(ShirtSize.sort_order, ShirtSize.code)
        )
        if active_only:
            query = query.where(ShirtSize.active.is_(True))
        return (await db.execute(query)).all()

    @staticmethod
    async def create_shirt_size(db: AsyncSession, event_id: UUID, payload):
        event = await db.get(Event, event_id)
        if not event or event.slug != HariSantriService.EVENT_SLUG:
            raise NotFoundException("HARI_SANTRI_EVENT_NOT_FOUND", "Event Hari Santri tidak ditemukan")
        row = ShirtSize(
            event_id=event_id,
            code=payload.code.upper(),
            label=payload.label,
            size_chart=payload.size_chart,
            active=payload.active,
            sort_order=payload.sort_order,
        )
        db.add(row)
        await db.flush()
        inventory = ShirtInventory(
            event_id=event_id,
            size_id=row.id,
            capacity=payload.capacity,
            reserved=0,
            allocated=0,
        )
        db.add(inventory)
        await db.commit()
        await db.refresh(row)
        await db.refresh(inventory)
        return row, inventory

    @staticmethod
    async def update_shirt_size(db: AsyncSession, size_id: UUID, payload):
        row = await db.get(ShirtSize, size_id, with_for_update=True)
        if not row or not (event := await db.get(Event, row.event_id)) or event.slug != HariSantriService.EVENT_SLUG:
            raise NotFoundException("SHIRT_SIZE_NOT_FOUND", "Ukuran kaos tidak ditemukan")
        inventory = (await db.execute(
            select(ShirtInventory)
            .where(ShirtInventory.size_id == size_id, ShirtInventory.event_id == row.event_id)
            .with_for_update()
        )).scalar_one_or_none()
        if not inventory:
            raise NotFoundException("SHIRT_INVENTORY_NOT_FOUND", "Inventori ukuran kaos tidak ditemukan")
        if payload.capacity < inventory.reserved + inventory.allocated:
            raise ConflictException("SHIRT_CAPACITY_BELOW_COMMITTED", "Kapasitas tidak boleh di bawah stok yang dipesan dan dialokasikan")
        row.code = payload.code.upper()
        row.label = payload.label
        row.size_chart = payload.size_chart
        row.active = payload.active
        row.sort_order = payload.sort_order
        inventory.capacity = payload.capacity
        await db.commit()
        await db.refresh(row)
        await db.refresh(inventory)
        return row, inventory

    @staticmethod
    async def replace_order_participants(
        db: AsyncSession,
        order_id: UUID,
        user_id: UUID,
        payload: list[OrderParticipantWrite],
    ) -> list[OrderParticipant]:
        order = (await db.execute(
            select(Order).where(Order.id == order_id).with_for_update()
        )).scalar_one_or_none()
        if not order or order.user_id != user_id:
            raise NotFoundException("ORDER_NOT_FOUND", "Order tidak ditemukan untuk akun ini")
        event = await db.get(Event, order.event_id, with_for_update=True) if order.event_id else None
        if not event or event.slug != HariSantriService.EVENT_SLUG:
            raise ValidationException("NOT_HARI_SANTRI_ORDER", "Order ini bukan order Hari Santri 2026")
        if order.status not in HariSantriService.EDITABLE_ORDER_STATUSES:
            raise ConflictException("ORDER_PARTICIPANTS_LOCKED", "Peserta hanya dapat diubah sebelum pembayaran")
        now = datetime.now(timezone.utc)
        if order.expires_at and order.expires_at <= now:
            raise ConflictException("ORDER_EXPIRED", "Masa reservasi order telah berakhir")
        activities = {participant.activity_type for participant in payload}
        if len(activities) != 1:
            raise ValidationException("MIXED_ACTIVITY_ORDER", "Satu order hanya boleh memuat satu jenis kegiatan")
        order_items = list((await db.execute(
            select(OrderItem).where(OrderItem.order_id == order.id)
        )).scalars().all())
        if len(order_items) != 1 or order_items[0].product_type != "hari_santri_package" or order_items[0].quantity != 1:
            raise ValidationException("INVALID_HARI_SANTRI_PACKAGE", "Order harus berisi satu paket Hari Santri")
        package_metadata = order_items[0].metadata_json or {}
        if package_metadata.get("activity_type") not in activities:
            raise ValidationException("PACKAGE_ACTIVITY_MISMATCH", "Jenis kegiatan peserta tidak sesuai dengan paket")
        minimum = int(package_metadata.get("min_participants", 1))
        maximum = int(package_metadata.get("max_participants", 20))
        if not minimum <= len(payload) <= maximum:
            raise ValidationException("INVALID_PARTICIPANT_COUNT", "Jumlah peserta tidak sesuai batas paket")
        people_capacity = int(package_metadata.get("capacity_people", 0))
        if people_capacity > 0:
            committed_people = await db.scalar(
                select(func.count(OrderParticipant.id))
                .join(Order, Order.id == OrderParticipant.order_id)
                .where(
                    Order.event_id == event.id,
                    Order.id != order.id,
                    OrderParticipant.status.in_(["reserved", "confirmed"]),
                    or_(
                        Order.status == OrderStatus.PAID,
                        and_(
                            Order.status.in_([OrderStatus.DRAFT, OrderStatus.PENDING, OrderStatus.PARTIALLY_PAID]),
                            or_(Order.expires_at.is_(None), Order.expires_at > now),
                        ),
                    ),
                )
            )
            if int(committed_people or 0) + len(payload) > people_capacity:
                raise ConflictException("EVENT_CAPACITY_EXCEEDED", "Kapasitas peserta event sudah tercapai")
        for participant in payload:
            if not all((participant.province_code, participant.regency_code, participant.district_code, participant.village_code)):
                raise ValidationException("REGION_REQUIRED", "Provinsi sampai desa wajib dipilih untuk setiap peserta")
            await RegionService.validate_chain(
                db,
                participant.province_code,
                participant.regency_code,
                participant.district_code,
                participant.village_code,
            )
            if participant.birth_date and participant.birth_date > now.date():
                raise ValidationException("INVALID_BIRTH_DATE", "Tanggal lahir tidak boleh di masa depan")
            if participant.birth_date and (now.date() - participant.birth_date).days < 18 * 365:
                if not participant.guardian_name or not participant.guardian_contact:
                    raise ValidationException("GUARDIAN_REQUIRED", "Nama dan kontak wali wajib untuk peserta di bawah 18 tahun")

        previous = list((await db.execute(
            select(OrderParticipant)
            .where(OrderParticipant.order_id == order.id)
            .order_by(OrderParticipant.participant_number)
            .with_for_update()
        )).scalars().all())
        previous_counts = Counter(row.shirt_size_id for row in previous if row.shirt_size_id)
        requested_codes = {participant.shirt_size_code.upper() for participant in payload}
        sizes = list((await db.execute(
            select(ShirtSize).where(
                ShirtSize.event_id == event.id,
                ShirtSize.code.in_(requested_codes),
                ShirtSize.active.is_(True),
            )
        )).scalars().all())
        sizes_by_code = {size.code: size for size in sizes}
        if requested_codes != set(sizes_by_code):
            raise ValidationException("INVALID_SHIRT_SIZE", "Satu atau lebih ukuran kaos tidak aktif atau tidak tersedia")

        new_counts = Counter(sizes_by_code[participant.shirt_size_code.upper()].id for participant in payload)
        inventory_size_ids = set(previous_counts) | set(new_counts)
        inventories = list((await db.execute(
            select(ShirtInventory)
            .where(ShirtInventory.event_id == event.id, ShirtInventory.size_id.in_(inventory_size_ids))
            .order_by(ShirtInventory.size_id)
            .with_for_update()
        )).scalars().all())
        inventory_by_size = {row.size_id: row for row in inventories}
        if inventory_size_ids != set(inventory_by_size):
            raise ConflictException("SHIRT_INVENTORY_NOT_CONFIGURED", "Inventori belum disiapkan untuk ukuran yang dipilih")
        for size_id in inventory_size_ids:
            inventory = inventory_by_size[size_id]
            prior = previous_counts[size_id]
            requested = new_counts[size_id]
            available_after_replace = inventory.capacity - inventory.allocated - inventory.reserved + prior
            if available_after_replace < requested:
                raise ConflictException("SHIRT_SIZE_OUT_OF_STOCK", "Stok ukuran kaos tidak mencukupi")

        await db.execute(delete(OrderParticipant).where(OrderParticipant.order_id == order.id))
        for size_id in inventory_size_ids:
            inventory = inventory_by_size[size_id]
            inventory.reserved += new_counts[size_id] - previous_counts[size_id]
        if order.expires_at is None:
            order.expires_at = now + timedelta(minutes=get_settings().HARI_SANTRI_RESERVATION_MINUTES)

        result = []
        for number, participant in enumerate(payload, start=1):
            size = sizes_by_code[participant.shirt_size_code.upper()]
            row = OrderParticipant(
                order_id=order.id,
                participant_number=number,
                full_name=participant.full_name.strip(),
                birth_date=participant.birth_date,
                guardian_name=participant.guardian_name.strip() if participant.guardian_name else None,
                guardian_contact=participant.guardian_contact.strip() if participant.guardian_contact else None,
                province_code=participant.province_code,
                regency_code=participant.regency_code,
                district_code=participant.district_code,
                village_code=participant.village_code,
                activity_type=participant.activity_type,
                shirt_size_id=size.id,
                shirt_size_code_snapshot=size.code,
                status="reserved",
            )
            db.add(row)
            result.append(row)
        await db.commit()
        for row in result:
            await db.refresh(row)
        return result

    @staticmethod
    async def get_order_participants(db: AsyncSession, order_id: UUID, user_id: UUID):
        order = await db.get(Order, order_id)
        if not order or order.user_id != user_id:
            raise NotFoundException("ORDER_NOT_FOUND", "Order tidak ditemukan untuk akun ini")
        return list((await db.execute(
            select(OrderParticipant)
            .where(OrderParticipant.order_id == order_id)
            .order_by(OrderParticipant.participant_number)
        )).scalars().all())

    @staticmethod
    async def create_checkout(db: AsyncSession, order_id: UUID, user_id: UUID) -> dict:
        order = (await db.execute(
            select(Order).where(Order.id == order_id).with_for_update()
        )).scalar_one_or_none()
        if not order or order.user_id != user_id:
            raise NotFoundException("ORDER_NOT_FOUND", "Order tidak ditemukan untuk akun ini")
        event = await db.get(Event, order.event_id) if order.event_id else None
        if not event or event.slug != HariSantriService.EVENT_SLUG:
            raise ValidationException("NOT_HARI_SANTRI_ORDER", "Order ini bukan order Hari Santri 2026")
        participant_count = (await db.execute(
            select(OrderParticipant.id).where(OrderParticipant.order_id == order.id).limit(1)
        )).scalar_one_or_none()
        if not participant_count:
            raise ValidationException("ORDER_PARTICIPANTS_REQUIRED", "Data peserta dan ukuran kaos wajib dilengkapi sebelum checkout")
        if order.status == OrderStatus.PAID:
            return {"already_paid": True, "status": "PAID", "payment_url": None}
        if order.status in {OrderStatus.CANCELED, OrderStatus.EXPIRED, "failed", "paid_needs_review"}:
            raise ConflictException("ORDER_NOT_PAYABLE", "Order tidak dapat dibayar melalui checkout")

        amount = Decimal(str(order.total_amount))
        if order.currency != "IDR" or amount <= 0 or amount != amount.to_integral_value():
            raise ValidationException("HARI_SANTRI_AMOUNT_INVALID", "Order Hari Santri harus bernilai positif dalam rupiah bulat")
        user = await db.get(User, user_id)
        if not user or not user.email:
            raise ValidationException("CUSTOMER_EMAIL_REQUIRED", "Email pemesan wajib untuk pembayaran")
        items = list((await db.execute(
            select(OrderItem).where(OrderItem.order_id == order.id).order_by(OrderItem.id)
        )).scalars().all())
        package_code = items[0].product_code if items else "HARI-SANTRI"
        payment = (await db.execute(
            select(HariSantriPayment).where(HariSantriPayment.order_id == order.id).with_for_update()
        )).scalar_one_or_none()
        if payment and payment.status.upper() == "PAID":
            return {"already_paid": True, "status": "PAID", "payment_id": payment.payment_portal_id, "payment_no": payment.payment_no, "payment_url": None}
        if payment and payment.payment_url and payment.status.upper() in {"CREATED", "PENDING", "INITIATED", "UNKNOWN"}:
            return HariSantriService._checkout_read(payment)

        if payment is None:
            reference_id = order.order_number
            payment = HariSantriPayment(
                order_id=order.id,
                reference_id=reference_id,
                amount=amount,
                currency="IDR",
                status="creating",
                expires_at=order.expires_at,
            )
            db.add(payment)
            await db.commit()
            await db.refresh(payment)

        settings = PaymentPortalClient._settings()
        return_url = settings.PAYMENT_PORTAL_RETURN_URL
        payload = {
            "service_code": settings.PAYMENT_PORTAL_SERVICE_CODE,
            "event_id": str(event.id),
            "event_name": event.name,
            "reference_id": payment.reference_id,
            "description": f"Hari Santri 2026 - {package_code}",
            "amount": int(amount),
            "currency": "IDR",
            "customer": {"name": user.full_name or user.email, "email": user.email.strip().lower()},
            "return_url": return_url,
            "metadata": {"event_id": str(event.id), "order_id": str(order.id), "package_code": package_code},
        }
        if order.expires_at:
            expiry = order.expires_at
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            payload["expires_at"] = expiry.isoformat().replace("+00:00", "Z")
        idempotency_key = f"hari-santri-order-{order.id}"
        try:
            data = (
                await PaymentPortalClient.create_payment(payload, idempotency_key)
                if not payment.payment_portal_id
                else await PaymentPortalClient.renew_checkout(payment.payment_portal_id)
            )
        except AppException:
            payment.status = "unknown"
            await db.commit()
            raise
        if data.get("reference_id") not in {None, payment.reference_id}:
            raise AppException("PAYMENT_PORTAL_REFERENCE_MISMATCH", "Reference pembayaran dari Payment Portal tidak cocok")
        if data.get("amount") is not None and int(data["amount"]) != int(amount):
            raise AppException("PAYMENT_PORTAL_AMOUNT_MISMATCH", "Nominal pembayaran dari Payment Portal tidak cocok")
        payment.payment_portal_id = str(data.get("payment_id") or payment.payment_portal_id or "") or None
        payment.payment_no = str(data.get("payment_no") or payment.payment_no or "") or None
        payment.payment_url = data.get("payment_url") or payment.payment_url
        payment.status = str(data.get("status") or "pending").upper()
        remote_expiry = data.get("expires_at")
        if isinstance(remote_expiry, str):
            try:
                remote_expiry = datetime.fromisoformat(remote_expiry.replace("Z", "+00:00"))
            except ValueError as exc:
                raise AppException("PAYMENT_PORTAL_INVALID_RESPONSE", "Format expiry dari Payment Portal tidak valid") from exc
        if remote_expiry is not None and not isinstance(remote_expiry, datetime):
            raise AppException("PAYMENT_PORTAL_INVALID_RESPONSE", "Format expiry dari Payment Portal tidak valid")
        payment.expires_at = remote_expiry or payment.expires_at
        await db.commit()
        await db.refresh(payment)
        return HariSantriService._checkout_read(payment)

    @staticmethod
    def _checkout_read(payment: HariSantriPayment) -> dict:
        return {
            "payment_id": payment.payment_portal_id,
            "payment_no": payment.payment_no,
            "reference_id": payment.reference_id,
            "payment_url": payment.payment_url,
            "status": payment.status,
            "expires_at": payment.expires_at,
            "already_paid": payment.status.upper() == "PAID",
        }

    @staticmethod
    async def payment_status(db: AsyncSession, order_id: UUID, user_id: UUID) -> dict:
        order = await db.get(Order, order_id)
        if not order or order.user_id != user_id:
            raise NotFoundException("ORDER_NOT_FOUND", "Order tidak ditemukan untuk akun ini")
        payment = (await db.execute(
            select(HariSantriPayment).where(HariSantriPayment.order_id == order.id)
        )).scalar_one_or_none()
        return {
            "order_id": order.id,
            "order_status": order.status,
            "payment_id": payment.payment_portal_id if payment else None,
            "payment_no": payment.payment_no if payment else None,
            "payment_status": payment.status if payment else "NOT_CREATED",
        }

    @staticmethod
    async def process_payment_callback(
        db: AsyncSession,
        raw_body: bytes,
        event_id: str,
        timestamp: str,
        signature: str,
    ) -> dict:
        payload = PaymentPortalClient.verify_callback(raw_body, event_id, timestamp, signature)
        payload_hash = hashlib.sha256(raw_body).hexdigest()
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:event_id))"),
            {"event_id": event_id},
        )
        existing_event = (await db.execute(
            select(HariSantriCallbackEvent).where(HariSantriCallbackEvent.event_id == event_id)
        )).scalar_one_or_none()
        if existing_event:
            if existing_event.payload_hash != payload_hash:
                raise ConflictException("PAYMENT_CALLBACK_EVENT_CONFLICT", "Event ID callback pernah digunakan untuk payload berbeda")
            return {"duplicate": True}

        data = payload["data"]
        reference_id = str(data.get("reference_id") or "")
        payment = (await db.execute(
            select(HariSantriPayment).where(HariSantriPayment.reference_id == reference_id).with_for_update()
        )).scalar_one_or_none()
        if not payment:
            raise NotFoundException("PAYMENT_REFERENCE_NOT_FOUND", "Reference order callback tidak ditemukan")
        order = (await db.execute(
            select(Order).where(Order.id == payment.order_id).with_for_update()
        )).scalar_one()
        if str(data.get("event_id") or "") != str(order.event_id):
            raise ConflictException("PAYMENT_CALLBACK_EVENT_MISMATCH", "Event callback tidak cocok dengan order")
        external_payment_id = str(data.get("payment_id") or "")
        callback_amount = data.get("amount")
        callback_currency = str(data.get("currency") or "").upper()
        if payment.payment_portal_id and external_payment_id != payment.payment_portal_id:
            raise ConflictException("PAYMENT_PORTAL_ID_MISMATCH", "Payment ID callback tidak cocok")
        if callback_amount is None or int(callback_amount) != int(payment.amount) or callback_currency != payment.currency:
            raise ConflictException("PAYMENT_CALLBACK_AMOUNT_MISMATCH", "Nominal atau currency callback tidak cocok")
        payment.payment_portal_id = external_payment_id or payment.payment_portal_id
        payment.payment_no = str(data.get("payment_no") or payment.payment_no or "") or None
        callback_status = str(data.get("status") or payload["event_type"]).upper()
        expected_event_type = f"payment.{callback_status.lower()}"
        if str(payload["event_type"]).lower() != expected_event_type:
            raise ConflictException("PAYMENT_CALLBACK_STATUS_MISMATCH", "Event type callback tidak cocok dengan status pembayaran")
        payment.status = callback_status

        participant_rows = list((await db.execute(
            select(OrderParticipant).where(OrderParticipant.order_id == order.id).with_for_update()
        )).scalars().all())
        size_counts = Counter(row.shirt_size_id for row in participant_rows if row.shirt_size_id)
        inventories = list((await db.execute(
            select(ShirtInventory)
            .where(ShirtInventory.event_id == order.event_id, ShirtInventory.size_id.in_(size_counts))
            .order_by(ShirtInventory.size_id)
            .with_for_update()
        )).scalars().all()) if size_counts else []
        inventory_by_size = {row.size_id: row for row in inventories}
        is_paid = callback_status == "PAID"
        if order.status == OrderStatus.PAID:
            pass
        elif is_paid:
            if not participant_rows or any(
                size_id not in inventory_by_size or inventory_by_size[size_id].reserved < count
                for size_id, count in size_counts.items()
            ):
                order.status = "paid_needs_review"
            else:
                for size_id, count in size_counts.items():
                    inventory = inventory_by_size[size_id]
                    inventory.reserved -= count
                    inventory.allocated += count
                for participant in participant_rows:
                    participant.status = "confirmed"
                    ticket = (await db.execute(
                        select(HariSantriTicket).where(HariSantriTicket.participant_id == participant.id)
                    )).scalar_one_or_none()
                    if ticket is None:
                        ticket_id = UUID(bytes=hashlib.sha256(f"{event_id}:{participant.id}".encode()).digest()[:16])
                        token = HariSantriService._ticket_token(ticket_id)
                        db.add(HariSantriTicket(
                            id=ticket_id,
                            participant_id=participant.id,
                            ticket_number=f"HS26-{ticket_id.hex[:16].upper()}",
                            qr_token_hash=hashlib.sha256(token.encode()).hexdigest(),
                            status="active",
                        ))
                order.status = OrderStatus.PAID
        elif callback_status in {"EXPIRED", "FAILED", "CANCELLED", "CANCELED"}:
            if order.status != OrderStatus.PAID:
                for size_id, count in size_counts.items():
                    inventory = inventory_by_size.get(size_id)
                    if inventory and inventory.reserved >= count:
                        inventory.reserved -= count
                for participant in participant_rows:
                    participant.status = callback_status.lower()
                order.status = OrderStatus.EXPIRED if callback_status == "EXPIRED" else OrderStatus.CANCELED

        db.add(HariSantriCallbackEvent(
            event_id=event_id,
            event_type=str(payload["event_type"]),
            payload_hash=payload_hash,
            payload=payload,
        ))
        await db.commit()
        return {"duplicate": False, "order_status": order.status}

    @staticmethod
    def _ticket_token(ticket_id: UUID) -> str:
        settings = get_settings()
        signature = hmac.new(settings.APP_SECRET_KEY.encode(), ticket_id.bytes, hashlib.sha256).digest()
        encoded_signature = base64.urlsafe_b64encode(signature).decode("ascii").rstrip("=")
        return f"{ticket_id}.{encoded_signature}"

    @staticmethod
    async def get_user_tickets(db: AsyncSession, user_id: UUID) -> list[dict]:
        rows = (await db.execute(
            select(HariSantriTicket, OrderParticipant)
            .join(OrderParticipant, OrderParticipant.id == HariSantriTicket.participant_id)
            .join(Order, Order.id == OrderParticipant.order_id)
            .where(Order.user_id == user_id, Order.status == OrderStatus.PAID)
            .order_by(HariSantriTicket.issued_at, HariSantriTicket.ticket_number)
        )).all()
        return [{
            "ticket_id": ticket.id,
            "ticket_number": ticket.ticket_number,
            "participant_name": participant.full_name,
            "activity_type": participant.activity_type,
            "status": ticket.status,
            "qr_token": HariSantriService._ticket_token(ticket.id),
            "issued_at": ticket.issued_at,
        } for ticket, participant in rows]

    @staticmethod
    async def checkin_ticket(db: AsyncSession, qr_token: str, staff_id: UUID) -> dict:
        token_hash = hashlib.sha256(qr_token.encode()).hexdigest()
        ticket = (await db.execute(
            select(HariSantriTicket).where(HariSantriTicket.qr_token_hash == token_hash).with_for_update()
        )).scalar_one_or_none()
        if not ticket:
            raise NotFoundException("HARI_SANTRI_TICKET_NOT_FOUND", "Tiket Hari Santri tidak ditemukan")
        if ticket.status != "active":
            raise ConflictException("HARI_SANTRI_TICKET_ALREADY_USED", "Tiket ini sudah digunakan atau tidak aktif")
        participant = await db.get(OrderParticipant, ticket.participant_id)
        order = await db.get(Order, participant.order_id) if participant else None
        if not participant or not order or order.status != OrderStatus.PAID:
            raise ConflictException("HARI_SANTRI_TICKET_NOT_ACTIVE", "Tiket belum aktif")
        now = datetime.now(timezone.utc)
        ticket.status = "used"
        ticket.checked_in_at = now
        db.add(HariSantriCheckin(ticket_id=ticket.id, scanned_by=staff_id, result="accepted"))
        await db.commit()
        return {"ticket_number": ticket.ticket_number, "participant_name": participant.full_name, "checked_in_at": now}

    @staticmethod
    async def create_bazaar_application(db: AsyncSession, event_id: UUID, user_id: UUID, payload) -> BazaarApplication:
        event = await db.get(Event, event_id)
        if not event or event.slug != HariSantriService.EVENT_SLUG:
            raise NotFoundException("HARI_SANTRI_EVENT_NOT_FOUND", "Event Hari Santri tidak ditemukan")
        existing = (await db.execute(select(BazaarApplication.id).where(
            BazaarApplication.event_id == event_id,
            BazaarApplication.user_id == user_id,
        ).limit(1))).scalar_one_or_none()
        if existing:
            raise ConflictException("BAZAAR_APPLICATION_EXISTS", "Akun ini sudah mengirim pengajuan tenant untuk event ini")
        application = BazaarApplication(
            event_id=event_id,
            user_id=user_id,
            business_name=payload.business_name.strip(),
            representative_name=payload.representative_name.strip(),
            contact_email=payload.contact_email.strip().lower(),
            contact_phone=payload.contact_phone.strip(),
            category=payload.category,
            product_summary=payload.product_summary.strip(),
            stall_needs=payload.stall_needs,
            status="submitted",
        )
        db.add(application)
        await db.commit()
        await db.refresh(application)
        return application

    @staticmethod
    async def list_my_bazaar_applications(db: AsyncSession, user_id: UUID) -> list[BazaarApplication]:
        return list((await db.execute(
            select(BazaarApplication)
            .where(BazaarApplication.user_id == user_id)
            .order_by(BazaarApplication.created_at.desc())
        )).scalars().all())

    @staticmethod
    async def list_bazaar_applications(db: AsyncSession, event_id: UUID) -> list[BazaarApplication]:
        return list((await db.execute(
            select(BazaarApplication)
            .where(BazaarApplication.event_id == event_id)
            .order_by(BazaarApplication.created_at.desc())
        )).scalars().all())

    @staticmethod
    async def decide_bazaar_application(db: AsyncSession, application_id: UUID, payload) -> BazaarApplication:
        application = await db.get(BazaarApplication, application_id, with_for_update=True)
        if not application:
            raise NotFoundException("BAZAAR_APPLICATION_NOT_FOUND", "Pengajuan tenant tidak ditemukan")
        application.status = payload.status
        application.organizer_notes = payload.organizer_notes.strip() if payload.organizer_notes else None
        await db.commit()
        await db.refresh(application)
        return application

    @staticmethod
    async def create_voucher(db: AsyncSession, participant_id: UUID, initial_balance: int, issued_by: UUID) -> dict:
        participant = (await db.execute(
            select(OrderParticipant).join(Order, Order.id == OrderParticipant.order_id)
            .where(OrderParticipant.id == participant_id, Order.status == OrderStatus.PAID)
        )).scalar_one_or_none()
        if not participant:
            raise NotFoundException("PARTICIPANT_NOT_FOUND", "Peserta berbayar tidak ditemukan")
        wallet = ParticipantWallet(participant_id=participant_id, balance=initial_balance)
        db.add(wallet)
        await db.flush()
        raw_token = HariSantriService._voucher_token(wallet.id)
        voucher = ParticipantVoucher(
            participant_id=participant_id, wallet_id=wallet.id,
            qr_token_hash=hashlib.sha256(raw_token.encode()).hexdigest(),
            initial_balance=initial_balance, expires_at=HariSantriService.VOUCHER_EXPIRES_AT, issued_by=issued_by,
        )
        db.add(voucher)
        HariSantriService._audit(db, issued_by, "voucher_issued", "participant_voucher", voucher.id, {"participant_id": str(participant_id), "initial_balance": initial_balance})
        await db.commit()
        await db.refresh(voucher)
        return HariSantriService._voucher_read(voucher, participant, wallet, raw_token)

    @staticmethod
    async def list_voucher_participants(db: AsyncSession) -> list[dict]:
        rows = (await db.execute(
            select(OrderParticipant, Order)
            .outerjoin(ParticipantVoucher, ParticipantVoucher.participant_id == OrderParticipant.id)
            .join(Order, Order.id == OrderParticipant.order_id)
            .where(Order.status == OrderStatus.PAID, ParticipantVoucher.id.is_(None))
            .order_by(OrderParticipant.full_name, OrderParticipant.participant_number)
        )).all()
        return [
            {
                "participant_id": participant.id,
                "participant_number": participant.participant_number,
                "participant_name": participant.full_name,
                "activity_type": participant.activity_type,
                "order_id": order.id,
            }
            for participant, order in rows
        ]

    @staticmethod
    async def list_vouchers(db: AsyncSession, status: str | None = None) -> list[dict]:
        query = (
            select(ParticipantVoucher, ParticipantWallet, OrderParticipant)
            .join(ParticipantWallet, ParticipantWallet.id == ParticipantVoucher.wallet_id)
            .join(OrderParticipant, OrderParticipant.id == ParticipantVoucher.participant_id)
            .join(Order, Order.id == OrderParticipant.order_id)
            .where(Order.status == OrderStatus.PAID)
            .order_by(ParticipantVoucher.issued_at.desc(), ParticipantVoucher.id.desc())
        )
        if status:
            query = query.where(ParticipantVoucher.status == status)
        rows = (await db.execute(query)).all()
        return [HariSantriService._voucher_read(voucher, participant, wallet) for voucher, wallet, participant in rows]

    @staticmethod
    async def revoke_voucher(db: AsyncSession, voucher_id: UUID, admin_id: UUID | None = None) -> dict:
        voucher = (await db.execute(select(ParticipantVoucher).where(ParticipantVoucher.id == voucher_id).with_for_update())).scalar_one_or_none()
        if not voucher:
            raise NotFoundException("VOUCHER_NOT_FOUND", "Kartu voucher tidak ditemukan")
        if voucher.status != "active":
            raise ConflictException("VOUCHER_NOT_ACTIVE", "Kartu voucher sudah tidak aktif")
        pending_balance = (await db.execute(select(ParticipantWallet.balance).where(ParticipantWallet.id == voucher.wallet_id))).scalar_one()
        if pending_balance != 0:
            raise ConflictException("VOUCHER_BALANCE_NOT_ZERO", "Saldo voucher harus nol sebelum dinonaktifkan")
        voucher.status = "revoked"
        HariSantriService._audit(db, admin_id, "voucher_revoked", "participant_voucher", voucher.id, {"status": voucher.status})
        await db.commit()
        return {"voucher_id": voucher.id, "status": voucher.status}

    @staticmethod
    def _voucher_read(voucher, participant, wallet, qr_token=None):
        return {
            "voucher_id": voucher.id, "participant_id": participant.id,
            "participant_name": participant.full_name, "balance": 0 if voucher.expires_at <= datetime.now(timezone.utc) else int(wallet.balance),
            "initial_balance": int(voucher.initial_balance), "status": "expired" if voucher.expires_at <= datetime.now(timezone.utc) else voucher.status,
            "expires_at": voucher.expires_at,
            "qr_token": qr_token, "issued_at": voucher.issued_at,
        }

    @staticmethod
    async def get_my_wallet(db: AsyncSession, user_id: UUID) -> dict:
        row = (await db.execute(
            select(ParticipantVoucher, ParticipantWallet, OrderParticipant)
            .join(ParticipantWallet, ParticipantWallet.id == ParticipantVoucher.wallet_id)
            .join(OrderParticipant, OrderParticipant.id == ParticipantVoucher.participant_id)
            .join(Order, Order.id == OrderParticipant.order_id)
            .where(Order.user_id == user_id, Order.status == OrderStatus.PAID)
            .order_by(ParticipantVoucher.issued_at.desc()).limit(1)
        )).first()
        if not row:
            raise NotFoundException("PARTICIPANT_VOUCHER_NOT_FOUND", "Kartu voucher peserta belum dibuat")
        voucher, wallet, participant = row
        return HariSantriService._voucher_read(voucher, participant, wallet, HariSantriService._voucher_token(voucher.wallet_id))

    @staticmethod
    async def list_my_wallets(db: AsyncSession, user_id: UUID) -> list[dict]:
        rows = (await db.execute(
            select(ParticipantVoucher, ParticipantWallet, OrderParticipant)
            .join(ParticipantWallet, ParticipantWallet.id == ParticipantVoucher.wallet_id)
            .join(OrderParticipant, OrderParticipant.id == ParticipantVoucher.participant_id)
            .join(Order, Order.id == OrderParticipant.order_id)
            .where(Order.user_id == user_id, Order.status == OrderStatus.PAID)
            .order_by(ParticipantVoucher.issued_at, ParticipantVoucher.id)
        )).all()
        return [HariSantriService._voucher_read(voucher, participant, wallet, HariSantriService._voucher_token(voucher.wallet_id)) for voucher, wallet, participant in rows]

    @staticmethod
    def _voucher_token(wallet_id: UUID) -> str:
        settings = get_settings()
        signature = hmac.new(settings.APP_SECRET_KEY.encode(), wallet_id.bytes, hashlib.sha256).digest()
        return f"{wallet_id}.{base64.urlsafe_b64encode(signature).decode('ascii').rstrip('=')}"

    @staticmethod
    async def transfer_voucher(db: AsyncSession, qr_token: str, amount: int, request_id: str, participant_password: str, exhibitor_id: UUID, scanned_by: UUID, *, is_admin: bool = False) -> dict:
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:request_id))"),
            {"request_id": request_id},
        )
        prior = (await db.execute(select(WalletTransfer).where(WalletTransfer.request_id == request_id))).scalar_one_or_none()
        if prior:
            if prior.exhibitor_id != exhibitor_id or int(prior.amount) != amount:
                raise ConflictException("WALLET_REQUEST_REUSED", "request_id sudah digunakan untuk transaksi lain")
            participant_wallet = (await db.execute(select(ParticipantWallet).where(ParticipantWallet.participant_id == prior.participant_id))).scalar_one_or_none()
            exhibitor_wallet = (await db.execute(select(ExhibitorWallet).where(ExhibitorWallet.exhibitor_id == prior.exhibitor_id))).scalar_one_or_none()
            return {"transfer_id": prior.id, "request_id": prior.request_id, "participant_id": prior.participant_id, "exhibitor_id": prior.exhibitor_id, "amount": int(prior.amount), "participant_balance": int(participant_wallet.balance) if participant_wallet else 0, "exhibitor_balance": int(exhibitor_wallet.balance) if exhibitor_wallet else 0, "created_at": prior.created_at}
        voucher = (await db.execute(select(ParticipantVoucher).where(ParticipantVoucher.qr_token_hash == hashlib.sha256(qr_token.encode()).hexdigest()).with_for_update())).scalar_one_or_none()
        now = datetime.now(timezone.utc)
        if not voucher or voucher.status != "active":
            raise NotFoundException("VOUCHER_NOT_FOUND", "QR voucher tidak ditemukan atau tidak aktif")
        if voucher.expires_at <= now:
            raise ConflictException("VOUCHER_EXPIRED", "Masa berlaku voucher sudah berakhir")
        participant = (await db.execute(
            select(OrderParticipant).join(Order, Order.id == OrderParticipant.order_id)
            .where(OrderParticipant.id == voucher.participant_id, Order.status == OrderStatus.PAID)
        )).scalar_one_or_none()
        exhibitor = (await db.execute(select(BazaarApplication).where(BazaarApplication.id == exhibitor_id, BazaarApplication.status != "rejected").with_for_update())).scalar_one_or_none()
        if not participant or not exhibitor:
            raise NotFoundException("EXHIBITOR_NOT_FOUND", "Exhibitor tidak ditemukan atau belum disetujui")
        if not is_admin and exhibitor.user_id != scanned_by:
            raise ValidationException("EXHIBITOR_ACCESS_DENIED", "Akun ini bukan pemilik exhibitor tersebut")
        participant_account = await db.get(User, (await db.get(Order, participant.order_id)).user_id)
        if not participant_account or not verify_password(participant_password, participant_account.password_hash):
            raise ValidationException("PARTICIPANT_CONFIRMATION_FAILED", "Password konfirmasi peserta tidak valid")
        wallet = (await db.execute(select(ParticipantWallet).where(ParticipantWallet.id == voucher.wallet_id).with_for_update())).scalar_one()
        exhibitor_wallet = (await db.execute(select(ExhibitorWallet).where(ExhibitorWallet.exhibitor_id == exhibitor_id).with_for_update())).scalar_one_or_none()
        if not exhibitor_wallet:
            exhibitor_wallet = ExhibitorWallet(exhibitor_id=exhibitor_id, balance=0)
            db.add(exhibitor_wallet)
            await db.flush()
        if wallet.balance < amount:
            raise ConflictException("INSUFFICIENT_VOUCHER_BALANCE", "Saldo voucher peserta tidak mencukupi")
        wallet.balance -= amount
        exhibitor_wallet.balance += amount
        transfer = WalletTransfer(request_id=request_id, voucher_id=voucher.id, participant_id=participant.id, exhibitor_id=exhibitor_id, amount=amount, scanned_by=scanned_by)
        db.add(transfer)
        HariSantriService._audit(db, scanned_by, "voucher_scanned", "wallet_transfer", transfer.id, {"voucher_id": str(voucher.id), "participant_id": str(participant.id), "exhibitor_id": str(exhibitor_id), "amount": amount, "request_id": request_id})
        await db.commit()
        await db.refresh(transfer)
        return {"transfer_id": transfer.id, "request_id": request_id, "participant_id": participant.id, "exhibitor_id": exhibitor_id, "amount": amount, "participant_balance": int(wallet.balance), "exhibitor_balance": int(exhibitor_wallet.balance), "created_at": transfer.created_at}

    @staticmethod
    async def get_exhibitor_wallet(db: AsyncSession, exhibitor_id: UUID, user_id: UUID, *, is_admin: bool = False) -> dict:
        exhibitor = await db.get(BazaarApplication, exhibitor_id)
        if not exhibitor or (not is_admin and exhibitor.user_id != user_id):
            raise NotFoundException("EXHIBITOR_NOT_FOUND", "Exhibitor tidak ditemukan")
        wallet = (await db.execute(select(ExhibitorWallet).where(ExhibitorWallet.exhibitor_id == exhibitor_id))).scalar_one_or_none()
        return {"exhibitor_id": exhibitor_id, "balance": int(wallet.balance) if wallet else 0}

    @staticmethod
    async def list_my_exhibitors(db: AsyncSession, user_id: UUID) -> list[dict]:
        rows = (await db.execute(
            select(BazaarApplication, ExhibitorWallet)
            .outerjoin(ExhibitorWallet, ExhibitorWallet.exhibitor_id == BazaarApplication.id)
            .where(BazaarApplication.user_id == user_id, BazaarApplication.status != "rejected")
            .order_by(BazaarApplication.created_at.desc())
        )).all()
        return [{"exhibitor_id": exhibitor.id, "business_name": exhibitor.business_name, "status": exhibitor.status, "balance": int(wallet.balance) if wallet else 0} for exhibitor, wallet in rows]

    @staticmethod
    async def get_my_exhibitor(db: AsyncSession, user_id: UUID, exhibitor_id: UUID | None = None) -> BazaarApplication:
        query = select(BazaarApplication).where(BazaarApplication.user_id == user_id, BazaarApplication.status != "rejected")
        if exhibitor_id:
            query = query.where(BazaarApplication.id == exhibitor_id)
        exhibitor = (await db.execute(
            query
            .order_by(BazaarApplication.created_at.desc())
        )).scalars().first()
        if not exhibitor:
            raise NotFoundException("EXHIBITOR_NOT_FOUND", "Lapak exhibitor akun ini tidak ditemukan")
        return exhibitor

    @staticmethod
    async def list_wallet_transfers(db: AsyncSession, exhibitor_id: UUID, user_id: UUID, *, is_admin: bool = False, date_from: date | None = None, date_to: date | None = None) -> list[dict]:
        exhibitor = await db.get(BazaarApplication, exhibitor_id)
        if not exhibitor or (not is_admin and exhibitor.user_id != user_id):
            raise NotFoundException("EXHIBITOR_NOT_FOUND", "Exhibitor tidak ditemukan")
        query = select(WalletTransfer).where(WalletTransfer.exhibitor_id == exhibitor_id)
        if date_from:
            query = query.where(WalletTransfer.created_at >= datetime.combine(date_from, datetime.min.time(), tzinfo=timezone.utc))
        if date_to:
            query = query.where(WalletTransfer.created_at < datetime.combine(date_to + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc))
        rows = (await db.execute(query.order_by(WalletTransfer.created_at.desc(), WalletTransfer.id.desc()))).scalars().all()
        return [{
            "transfer_id": row.id, "participant_id": row.participant_id,
            "exhibitor_id": row.exhibitor_id, "amount": int(row.amount),
            "direction": "credit", "created_at": row.created_at,
        } for row in rows]

    @staticmethod
    async def list_participant_transfers(db: AsyncSession, user_id: UUID) -> list[dict]:
        rows = (await db.execute(
            select(WalletTransfer)
            .join(OrderParticipant, OrderParticipant.id == WalletTransfer.participant_id)
            .join(Order, Order.id == OrderParticipant.order_id)
            .where(Order.user_id == user_id)
            .order_by(WalletTransfer.created_at.desc(), WalletTransfer.id.desc())
        )).scalars().all()
        return [{
            "transfer_id": row.id, "participant_id": row.participant_id,
            "exhibitor_id": row.exhibitor_id, "amount": int(row.amount),
            "direction": "debit", "created_at": row.created_at,
        } for row in rows]

    @staticmethod
    async def adjust_voucher(db: AsyncSession, voucher_id: UUID, amount: int, reason: str, admin_id: UUID) -> dict:
        voucher = (await db.execute(select(ParticipantVoucher).where(ParticipantVoucher.id == voucher_id).with_for_update())).scalar_one_or_none()
        if not voucher:
            raise NotFoundException("VOUCHER_NOT_FOUND", "Kartu voucher tidak ditemukan")
        if voucher.status != "active" or voucher.expires_at <= datetime.now(timezone.utc):
            raise ConflictException("VOUCHER_NOT_ACTIVE", "Voucher tidak aktif atau sudah kedaluwarsa")
        wallet = (await db.execute(select(ParticipantWallet).where(ParticipantWallet.id == voucher.wallet_id).with_for_update())).scalar_one()
        if wallet.balance + amount < 0:
            raise ConflictException("VOUCHER_BALANCE_NEGATIVE", "Penyesuaian membuat saldo voucher negatif")
        wallet.balance += amount
        db.add(WalletAdjustment(voucher_id=voucher.id, amount=amount, reason=reason.strip(), adjusted_by=admin_id))
        HariSantriService._audit(db, admin_id, "voucher_balance_adjusted", "participant_voucher", voucher.id, {"amount": amount, "balance": int(wallet.balance), "reason": reason.strip()})
        await db.commit()
        return {"voucher_id": voucher.id, "amount": amount, "balance": int(wallet.balance), "reason": reason.strip()}

    @staticmethod
    async def create_settlement(db: AsyncSession, exhibitor_id: UUID, amount: int, payment_reference: str | None, notes: str | None, admin_id: UUID | None = None) -> dict:
        exhibitor = (await db.execute(select(BazaarApplication).where(BazaarApplication.id == exhibitor_id, BazaarApplication.status != "rejected"))).scalar_one_or_none()
        if not exhibitor:
            raise NotFoundException("EXHIBITOR_NOT_FOUND", "Exhibitor tidak ditemukan atau belum disetujui")
        wallet = (await db.execute(select(ExhibitorWallet).where(ExhibitorWallet.exhibitor_id == exhibitor_id).with_for_update())).scalar_one_or_none()
        if not wallet or wallet.balance < amount:
            raise ConflictException("SETTLEMENT_EXCEEDS_BALANCE", "Nominal settlement melebihi saldo exhibitor")
        pending_total = (await db.scalar(select(func.coalesce(func.sum(ExhibitorSettlement.amount), 0)).where(ExhibitorSettlement.exhibitor_id == exhibitor_id, ExhibitorSettlement.status == "pending"))) or 0
        if pending_total + amount > wallet.balance:
            raise ConflictException("SETTLEMENT_EXCEEDS_AVAILABLE_BALANCE", "Nominal settlement melebihi saldo settlement yang tersedia")
        settlement = ExhibitorSettlement(exhibitor_id=exhibitor_id, amount=amount, payment_reference=payment_reference, notes=notes, status="pending")
        db.add(settlement)
        await db.flush()
        HariSantriService._audit(db, admin_id, "settlement_requested", "exhibitor_settlement", settlement.id, {"exhibitor_id": str(exhibitor_id), "amount": amount})
        await db.commit()
        await db.refresh(settlement)
        return HariSantriService._settlement_read(settlement)

    @staticmethod
    async def create_full_settlement(db: AsyncSession, exhibitor_id: UUID, payment_reference: str | None, notes: str | None, actor_user_id: UUID) -> dict:
        wallet = (await db.execute(select(ExhibitorWallet).where(ExhibitorWallet.exhibitor_id == exhibitor_id).with_for_update())).scalar_one_or_none()
        balance = int(wallet.balance) if wallet else 0
        pending_total = int((await db.scalar(select(func.coalesce(func.sum(ExhibitorSettlement.amount), 0)).where(ExhibitorSettlement.exhibitor_id == exhibitor_id, ExhibitorSettlement.status == "pending"))) or 0)
        amount = balance - pending_total
        if amount <= 0:
            raise ConflictException("SETTLEMENT_BALANCE_EMPTY", "Tidak ada saldo baru yang dapat diajukan untuk settlement")
        return await HariSantriService.create_settlement(db, exhibitor_id, amount, payment_reference, notes, actor_user_id)

    @staticmethod
    async def list_approved_exhibitors(db: AsyncSession) -> list[dict]:
        rows = (await db.execute(
            select(BazaarApplication, ExhibitorWallet)
            .outerjoin(ExhibitorWallet, ExhibitorWallet.exhibitor_id == BazaarApplication.id)
            .where(BazaarApplication.status != "rejected")
            .order_by(BazaarApplication.business_name)
        )).all()
        return [{"exhibitor_id": exhibitor.id, "business_name": exhibitor.business_name, "balance": int(wallet.balance) if wallet else 0} for exhibitor, wallet in rows]

    @staticmethod
    async def list_settlements(db: AsyncSession) -> list[dict]:
        rows = (await db.execute(
            select(ExhibitorSettlement, BazaarApplication.business_name)
            .join(BazaarApplication, BazaarApplication.id == ExhibitorSettlement.exhibitor_id)
            .order_by(ExhibitorSettlement.requested_at.desc(), ExhibitorSettlement.id.desc())
        )).all()
        return [{**HariSantriService._settlement_read(settlement), "business_name": business_name} for settlement, business_name in rows]

    @staticmethod
    async def list_settlement_reconciliation(db: AsyncSession) -> list[dict]:
        exhibitors = (await db.execute(
            select(BazaarApplication).where(BazaarApplication.status != "rejected").order_by(BazaarApplication.business_name)
        )).scalars().all()
        report = []
        for exhibitor in exhibitors:
            total_credits = int((await db.scalar(select(func.coalesce(func.sum(WalletTransfer.amount), 0)).where(WalletTransfer.exhibitor_id == exhibitor.id))) or 0)
            pending = int((await db.scalar(select(func.coalesce(func.sum(ExhibitorSettlement.amount), 0)).where(ExhibitorSettlement.exhibitor_id == exhibitor.id, ExhibitorSettlement.status == "pending"))) or 0)
            confirmed = int((await db.scalar(select(func.coalesce(func.sum(ExhibitorSettlement.amount), 0)).where(ExhibitorSettlement.exhibitor_id == exhibitor.id, ExhibitorSettlement.status == "confirmed"))) or 0)
            wallet = (await db.scalar(select(ExhibitorWallet.balance).where(ExhibitorWallet.exhibitor_id == exhibitor.id))) or 0
            wallet_balance = int(wallet)
            expected_balance = total_credits - confirmed
            report.append({"exhibitor_id": exhibitor.id, "business_name": exhibitor.business_name, "total_credits": total_credits, "pending_settlement": pending, "confirmed_settlement": confirmed, "wallet_balance": wallet_balance, "expected_balance": expected_balance, "status": "matched" if wallet_balance == expected_balance else "mismatch"})
        return report

    @staticmethod
    def settlement_reconciliation_csv(rows: list[dict]) -> str:
        output = io.StringIO()
        fields = ["exhibitor_id", "business_name", "total_credits", "pending_settlement", "confirmed_settlement", "wallet_balance", "expected_balance", "status"]
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({**row, "exhibitor_id": str(row["exhibitor_id"])})
        return output.getvalue()

    @staticmethod
    async def list_audit_logs(db: AsyncSession, limit: int = 200, action: str | None = None) -> list[dict]:
        query = (
            select(HariSantriAuditLog, User.full_name)
            .outerjoin(User, User.id == HariSantriAuditLog.actor_user_id)
            .order_by(HariSantriAuditLog.created_at.desc(), HariSantriAuditLog.id.desc())
            .limit(limit)
        )
        if action:
            query = query.where(HariSantriAuditLog.action == action)
        rows = (await db.execute(query)).all()
        return [
            {
                "id": row.id,
                "actor_user_id": row.actor_user_id,
                "actor_name": actor_name,
                "action": row.action,
                "entity_type": row.entity_type,
                "entity_id": row.entity_id,
                "payload": row.payload,
                "created_at": row.created_at,
            }
            for row, actor_name in rows
        ]

    @staticmethod
    async def confirm_settlement(db: AsyncSession, settlement_id: UUID, actor_user_id: UUID, *, is_admin: bool = False) -> dict:
        settlement = (await db.execute(select(ExhibitorSettlement).where(ExhibitorSettlement.id == settlement_id).with_for_update())).scalar_one_or_none()
        if not settlement:
            raise NotFoundException("SETTLEMENT_NOT_FOUND", "Settlement tidak ditemukan")
        if settlement.status != "pending":
            raise ConflictException("SETTLEMENT_ALREADY_CONFIRMED", "Settlement sudah dikonfirmasi")
        if not is_admin:
            exhibitor = await db.get(BazaarApplication, settlement.exhibitor_id)
            if not exhibitor or exhibitor.user_id != actor_user_id:
                raise ValidationException("SETTLEMENT_ACCESS_DENIED", "Settlement bukan milik lapak akun ini")
        wallet = (await db.execute(select(ExhibitorWallet).where(ExhibitorWallet.exhibitor_id == settlement.exhibitor_id).with_for_update())).scalar_one_or_none()
        if not wallet or wallet.balance < settlement.amount:
            raise ConflictException("SETTLEMENT_EXCEEDS_BALANCE", "Saldo exhibitor tidak mencukupi")
        wallet.balance -= settlement.amount
        settlement.status = "confirmed"
        settlement.confirmed_by = actor_user_id
        settlement.confirmed_at = datetime.now(timezone.utc)
        HariSantriService._audit(db, actor_user_id, "settlement_confirmed", "exhibitor_settlement", settlement.id, {"exhibitor_id": str(settlement.exhibitor_id), "amount": int(settlement.amount)})
        await db.commit()
        return HariSantriService._settlement_read(settlement)

    @staticmethod
    def _settlement_read(row):
        return {"settlement_id": row.id, "exhibitor_id": row.exhibitor_id, "amount": int(row.amount), "status": row.status, "payment_reference": row.payment_reference, "notes": row.notes, "requested_at": row.requested_at, "confirmed_at": row.confirmed_at}

    @staticmethod
    def wallet_transfers_csv(rows: list[dict]) -> str:
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=["transfer_id", "participant_id", "exhibitor_id", "amount", "direction", "created_at"])
        writer.writeheader()
        writer.writerows({**row, "transfer_id": str(row["transfer_id"]), "participant_id": str(row["participant_id"]), "exhibitor_id": str(row["exhibitor_id"]), "created_at": row["created_at"].isoformat()} for row in rows)
        return output.getvalue()
