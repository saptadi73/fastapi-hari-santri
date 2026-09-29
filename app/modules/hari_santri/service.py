from collections import Counter
from datetime import datetime, timedelta, timezone
import base64
import hashlib
import hmac
from decimal import Decimal
from uuid import UUID

from sqlalchemy import and_, delete, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import AppException, ConflictException, NotFoundException, ValidationException
from app.modules.events.models import Event
from app.modules.hari_santri.models import BazaarApplication, HariSantriCallbackEvent, HariSantriCheckin, HariSantriPayment, HariSantriTicket, OrderParticipant, ShirtInventory, ShirtSize
from app.modules.hari_santri.payment_portal import PaymentPortalClient
from app.modules.hari_santri.schemas import OrderParticipantWrite
from app.modules.payments.models import Order, OrderStatus
from app.modules.store.models import OrderItem
from app.modules.users.models import User


class HariSantriService:
    EVENT_SLUG = "hari-santri-2026"
    EDITABLE_ORDER_STATUSES = {OrderStatus.DRAFT, OrderStatus.PENDING}

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