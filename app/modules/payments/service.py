import json
import asyncio
import uuid
import re
import logging
from pathlib import Path
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.modules.payments import schemas
from app.modules.payments.models import DirectDebitBinding, Order, OrderKind, OrderStatus, Payment, PaymentProof, PaymentStatus, PaymentWebhookEvent, payment_allowed_actions
from app.modules.payments.repository import PaymentRepository
from app.modules.events.models import Event
from app.modules.participants.models import ParticipantProfile
from app.modules.iwbif.models import DelegatePackage, DelegatePackageRate, DelegateRegistrationDetail
from app.modules.store.models import Product
from app.modules.users.models import User
from app.core.config import get_settings
from app.core.exceptions import AppException, ConflictException, NotFoundException, ValidationException
from app.modules.payments.doku import DokuCheckoutClient, verify_signature
from app.modules.payments.midtrans import MidtransClient, normalize_midtrans_channel, verify_notification_signature
from app.modules.payments.doku_snap import DokuSnapClient, ensure_fresh_timestamp, issue_merchant_token, verify_asymmetric_signature, verify_merchant_token, verify_symmetric_signature
from app.modules.registrations.models import Registration, RegistrationStatus
from app.modules.email_notifications.service import deliver_payment_for_order
from app.modules.business_matching.models import Notification


logger = logging.getLogger(__name__)


class PaymentService:
    PAYMENT_PROOF_MIMES = {"image/jpeg": ".jpg", "image/png": ".png", "application/pdf": ".pdf"}
    MAX_PAYMENT_PROOF_SIZE = 10 * 1024 * 1024

    @staticmethod
    async def _reject_direct_gateway_for_hari_santri(session: AsyncSession, order: Order) -> None:
        event = await session.get(Event, order.event_id) if order.event_id else None
        if event and getattr(event, "slug", None) == "hari-santri-2026":
            raise ConflictException(
                "PAYMENT_PORTAL_REQUIRED",
                "Pembayaran Hari Santri harus dilanjutkan melalui Payment Portal",
            )

    @staticmethod
    def _segment_plan(total_amount: Decimal) -> list[Decimal]:
        """Return deterministic gateway chunks using the organizer's USD 500 rule."""
        total = Decimal(str(total_amount)).quantize(Decimal("0.01"))
        limit = Decimal(str(get_settings().QRIS_SEGMENT_LIMIT_IDR))
        if total <= limit:
            return [total]
        chunks: list[Decimal] = []
        remaining = total
        while remaining > 0:
            amount = min(limit, remaining)
            chunks.append(amount)
            remaining -= amount
        return chunks

    @staticmethod
    async def _payment_progress(session: AsyncSession, order: Order) -> tuple[Decimal, Decimal]:
        """Sum settled value once per logical segment, while preserving legacy rows."""
        payments = list((await session.execute(
            select(Payment).where(
                Payment.order_id == order.id,
                Payment.transaction_status == PaymentStatus.SUCCESS,
                Payment.deleted_at.is_(None),
            ).order_by(Payment.created_at, Payment.id)
        )).scalars().all())
        paid = Decimal("0")
        settled_sequences: set[int] = set()
        for payment in payments:
            if payment.payment_sequence is not None:
                if payment.payment_sequence in settled_sequences:
                    continue
                settled_sequences.add(payment.payment_sequence)
            paid += Decimal(str(payment.gross_amount))
        total = Decimal(str(order.total_amount))
        paid = min(paid, total)
        return paid, max(total - paid, Decimal("0"))

    @staticmethod
    async def _reconcile_order_payment(session: AsyncSession, order: Order) -> tuple[Decimal, Decimal, bool]:
        """Make the parent order/registration reflect aggregate settled funds."""
        previous_status = order.status
        paid, remaining = await PaymentService._payment_progress(session, order)
        if remaining == 0 and paid > 0:
            order.status = OrderStatus.PAID
        elif paid > 0:
            order.status = OrderStatus.PARTIALLY_PAID
        elif order.status not in {OrderStatus.CANCELED, OrderStatus.EXPIRED}:
            order.status = OrderStatus.PENDING
        if order.status in {OrderStatus.PAID, OrderStatus.PARTIALLY_PAID}:
            order.canceled_at = order.canceled_by = order.cancellation_reason = None
        if order.registration_id and order.order_kind != OrderKind.ADDITIONAL:
            registration = await session.get(Registration, order.registration_id, with_for_update=True)
            if registration and registration.status != RegistrationStatus.CONFIRMED:
                registration.status = (
                    RegistrationStatus.PAID
                    if order.status == OrderStatus.PAID
                    else RegistrationStatus.PAYMENT_PENDING
                )
        if order.status == OrderStatus.PAID and order.order_kind == OrderKind.ADDITIONAL:
            await PaymentService._activate_paid_additional_packages(session, order)
        return paid, remaining, previous_status != OrderStatus.PAID and order.status == OrderStatus.PAID

    @staticmethod
    async def _activate_paid_additional_packages(session: AsyncSession, order: Order) -> None:
        """Idempotently attach settled add-ons to the existing registration."""
        if not order.registration_id:
            raise ValidationException("ADDITIONAL_REGISTRATION_REQUIRED", "Order additional tidak terhubung ke registrasi")
        from app.modules.iwbif.models import DelegatePackage, DelegatePackageRate, DelegateRegistrationPackageSelection
        from app.modules.store.models import OrderItem, Product

        rows = (await session.execute(
            select(DelegatePackage, DelegatePackageRate, OrderItem)
            .join(DelegatePackageRate, DelegatePackageRate.delegate_package_id == DelegatePackage.id)
            .join(Product, Product.delegate_package_rate_id == DelegatePackageRate.id)
            .join(OrderItem, OrderItem.product_id == Product.id)
            .where(
                OrderItem.order_id == order.id,
                DelegatePackage.package_type == "additional",
                Product.product_type == "additional",
            )
        )).all()
        if not rows:
            raise ValidationException("ADDITIONAL_ORDER_ITEMS_REQUIRED", "Order additional tidak memiliki item additional yang valid")
        for package, rate, item in rows:
            existing = (await session.execute(select(DelegateRegistrationPackageSelection.id).where(
                DelegateRegistrationPackageSelection.registration_id == order.registration_id,
                DelegateRegistrationPackageSelection.delegate_package_id == package.id,
            ).limit(1))).scalar_one_or_none()
            if existing:
                continue
            snapshot = item.metadata_json or {}
            session.add(DelegateRegistrationPackageSelection(
                registration_id=order.registration_id,
                delegate_package_id=package.id,
                package_rate_id=rate.id,
                source_order_id=order.id,
                selection_role="additional",
                occupancy_type=snapshot.get("occupancy_type", rate.occupancy_type),
                package_code=snapshot.get("package_code", package.code),
                package_name=snapshot.get("package_name", package.name),
                rate_name=snapshot.get("rate_name", rate.name),
                selected_amount=snapshot.get("display_amount", rate.amount),
                selected_currency=snapshot.get("display_currency", rate.currency),
                selected_payment_amount=item.unit_price,
                payment_currency=item.currency,
            ))

    @staticmethod
    async def _next_payment_segment(session: AsyncSession, order: Order) -> tuple[int, int, Decimal]:
        plan = PaymentService._segment_plan(Decimal(str(order.total_amount)))
        successful = set((await session.execute(
            select(Payment.payment_sequence).where(
                Payment.order_id == order.id,
                Payment.transaction_status == PaymentStatus.SUCCESS,
                Payment.deleted_at.is_(None),
                Payment.payment_sequence.is_not(None),
            )
        )).scalars().all())
        for index, amount in enumerate(plan, start=1):
            if index not in successful:
                return index, len(plan), amount
        raise ConflictException("ORDER_ALREADY_PAID", "Seluruh bagian pembayaran order sudah lunas")

    @staticmethod
    async def submit_manual_payment_proof(session: AsyncSession, order_id: uuid.UUID, user_id: uuid.UUID, payment_method: str, transfer_reference: str | None, notes: str | None, file) -> tuple[Payment, PaymentProof]:
        order = await session.get(Order, order_id, with_for_update=True)
        if not order or order.user_id != user_id:
            raise NotFoundException("ORDER_NOT_FOUND", "Order tidak ditemukan untuk akun ini")
        await PaymentService._reject_direct_gateway_for_hari_santri(session, order)
        if order.status == OrderStatus.PAID:
            raise ConflictException("ORDER_ALREADY_PAID", "Order sudah dibayar")
        if order.status in {OrderStatus.CANCELED, OrderStatus.EXPIRED}:
            raise ConflictException("ORDER_NOT_PAYABLE", "Order dibatalkan atau kedaluwarsa")
        if payment_method not in {"manual_transfer", "manual_qr_code"}:
            raise ValidationException("INVALID_MANUAL_PAYMENT_METHOD", "Metode harus manual_transfer atau manual_qr_code")
        transfer_reference = transfer_reference.strip() if transfer_reference else None
        notes = notes.strip() if notes else None
        if transfer_reference and len(transfer_reference) > 128:
            raise ValidationException("INVALID_TRANSFER_REFERENCE", "Referensi transfer maksimal 128 karakter")
        if notes and len(notes) > 1000:
            raise ValidationException("INVALID_PAYMENT_PROOF_NOTES", "Catatan maksimal 1000 karakter")
        mime_type = file.content_type or ""
        extension = PaymentService.PAYMENT_PROOF_MIMES.get(mime_type)
        if not extension:
            raise ValidationException("INVALID_PAYMENT_PROOF_MIME", "Bukti pembayaran harus JPG, PNG, atau PDF")
        content = await file.read(PaymentService.MAX_PAYMENT_PROOF_SIZE + 1)
        await file.close()
        if not content or len(content) > PaymentService.MAX_PAYMENT_PROOF_SIZE:
            raise ValidationException("INVALID_PAYMENT_PROOF_SIZE", "Bukti pembayaran kosong atau melebihi 10 MB")

        payment = (await session.execute(select(Payment).where(
            Payment.order_id == order.id,
            Payment.provider.in_(["manual_transfer", "manual_qr_code"]),
            Payment.deleted_at.is_(None),
        ).order_by(Payment.created_at.desc()).with_for_update())).scalars().first()
        if payment is None:
            payment = Payment(order_id=order.id, provider=payment_method, gross_amount=order.total_amount, currency=order.currency)
            session.add(payment)
            await session.flush()
        payment.provider = payment_method
        payment.provider_order_id = order.order_number
        payment.provider_transaction_id = transfer_reference or payment.provider_transaction_id
        payment.provider_reference_no = transfer_reference or payment.provider_reference_no
        payment.payment_type = "qrcode" if payment_method == "manual_qr_code" else "bank_transfer"
        payment.channel_code = "MANUAL_QRIS" if payment_method == "manual_qr_code" else "MANUAL_TRANSFER"
        payment.transaction_status = PaymentStatus.PENDING
        if order.status == OrderStatus.DRAFT:
            order.status = OrderStatus.PENDING

        safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", Path(file.filename or "payment-proof").name)[:255]
        storage_key = f"payment-proofs/{order.id}/{uuid.uuid4()}{extension}"
        target = Path(".private_uploads").resolve() / storage_key
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        except OSError as exc:
            logger.exception(
                "Payment proof storage write failed: order_id=%s storage_root=%s",
                order.id,
                Path(".private_uploads").resolve(),
            )
            raise AppException(
                code="UPLOAD_STORAGE_ERROR",
                message="Penyimpanan bukti pembayaran tidak dapat ditulis oleh server",
            ) from exc
        proof = PaymentProof(payment_id=payment.id, uploaded_by=user_id, original_filename=safe_name, storage_key=storage_key, mime_type=mime_type, file_size=len(content), notes=notes)
        session.add(proof)
        await PaymentService._notify_payment_status(session, order, payment, user_id)
        try:
            await session.commit()
        except Exception:
            try:
                if target.is_file():
                    target.unlink()
            except OSError:
                logger.exception(
                    "Payment proof cleanup failed after database error: storage_key=%s",
                    storage_key,
                )
            raise
        await session.refresh(proof)
        return payment, proof
    @staticmethod
    def _reusable_midtrans_token(payment: Payment | None, now: datetime | None = None) -> str:
        """Return a Snap token only while the local payment window is still open."""
        if not payment or not payment.checkout_url:
            return ""
        if payment.transaction_status not in {PaymentStatus.CREATED, PaymentStatus.PENDING}:
            return ""
        if payment.expired_at is None:
            return ""

        current_time = now or datetime.now(timezone.utc)
        expires_at = payment.expired_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at <= current_time:
            return ""

        stored = json.loads(payment.raw_response or "{}")
        return str(stored.get("token") or "")

    @staticmethod
    def _reusable_doku_checkout_url(payment: Payment | None, now: datetime | None = None) -> str:
        if not payment or not payment.checkout_url:
            return ""
        if payment.transaction_status not in {PaymentStatus.CREATED, PaymentStatus.PENDING}:
            return ""
        if payment.expired_at is None:
            return ""
        current_time = now or datetime.now(timezone.utc)
        expires_at = payment.expired_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        return payment.checkout_url if expires_at > current_time else ""

    @staticmethod
    async def _admin_user_ids(db: AsyncSession):
        rows = (await db.execute(select(User.id).where(User.role.in_(["admin", "organizer"]), User.status == "active"))).all()
        return [row[0] for row in rows]

    @staticmethod
    async def _notify_payment_status(db: AsyncSession, order: Order, payment: Payment, actor_user_id: uuid.UUID | None = None):
        if not order.registration_id:
            return
        registration = await db.get(Registration, order.registration_id)
        if not registration:
            return
        event_id = registration.event_id
        payment_reference = payment.provider_transaction_id or payment.provider_order_id or payment.provider_reference_no or ""
        paid_amount, remaining_amount = await PaymentService._payment_progress(db, order)
        if remaining_amount > 0 and paid_amount > 0:
            participant_body = (
                f"Pembayaran bagian {payment.payment_sequence or '-'} untuk order {order.order_number} berhasil. "
                f"Total diterima Rp{paid_amount:,.0f}; sisa Rp{remaining_amount:,.0f}. "
                "Ticket dan proses lanjutan belum tersedia sampai seluruh pembayaran lunas. "
                "Pembagian diterapkan karena batas nominal transaksi QRIS Bank Indonesia."
            )
        elif remaining_amount == 0 and paid_amount > 0:
            participant_body = f"Seluruh pembayaran order {order.order_number} telah lunas. Ticket dan proses lanjutan kini eligible."
        else:
            participant_body = f"Pembayaran {payment.provider or 'provider'} untuk order {order.order_number} menjadi {payment.transaction_status}. Ref: {payment_reference or '-'}"
        recipients = [order.user_id]
        recipients.extend(await PaymentService._admin_user_ids(db))
        if actor_user_id:
            recipients = [recipient for recipient in recipients if recipient != actor_user_id]
        for user_id in dict.fromkeys(recipients):
            db.add(Notification(
                user_id=user_id,
                event_id=event_id,
                type="payment_status_update",
                title="Status pembayaran berubah",
                body=participant_body,
                entity_type="order",
                entity_id=order.id,
            ))

    @staticmethod
    async def confirm_manual_payment(session: AsyncSession, order_id: uuid.UUID, payload: schemas.ManualPaymentConfirmRequest, admin_user_id: uuid.UUID) -> tuple[Order, Payment]:
        order = await session.get(Order, order_id, with_for_update=True)
        if not order:
            raise NotFoundException("ORDER_NOT_FOUND", "Order tidak ditemukan")
        await PaymentService._reject_direct_gateway_for_hari_santri(session, order)
        manual_payment = (await session.execute(
            select(Payment)
            .where(
                Payment.order_id == order.id,
                Payment.provider.in_(["manual_transfer", "manual_qr_code"]),
                Payment.deleted_at.is_(None),
            )
            .order_by(Payment.created_at.desc())
            .with_for_update()
        )).scalars().first()
        if order.status == OrderStatus.PAID:
            if manual_payment:
                return order, manual_payment
            raise ConflictException("ORDER_ALREADY_PAID", "Order sudah dibayar melalui metode lain")
        if order.status in {OrderStatus.CANCELED, OrderStatus.EXPIRED}:
            raise ConflictException("ORDER_NOT_PAYABLE", "Order dibatalkan atau kedaluwarsa tidak dapat dikonfirmasi")

        paid_at = payload.paid_at or datetime.now(timezone.utc)
        confirmation = {"confirmed_by": str(admin_user_id), "payment_method": payload.payment_method, "payment_reference": payload.transfer_reference, "notes": payload.notes, "confirmed_at": paid_at.isoformat()}
        if manual_payment is None:
            manual_payment = Payment(order_id=order.id, provider=payload.payment_method, gross_amount=order.total_amount, currency=order.currency)
            session.add(manual_payment)
        manual_payment.provider = payload.payment_method
        manual_payment.provider_transaction_id = payload.transfer_reference
        manual_payment.provider_order_id = order.order_number
        manual_payment.provider_reference_no = payload.transfer_reference
        manual_payment.payment_type = "qrcode" if payload.payment_method == "manual_qr_code" else "bank_transfer"
        manual_payment.channel_code = "MANUAL_QR_CODE" if payload.payment_method == "manual_qr_code" else "MANUAL_TRANSFER"
        manual_payment.transaction_status = PaymentStatus.SUCCESS
        manual_payment.paid_at = paid_at
        manual_payment.raw_response = json.dumps(confirmation)
        await PaymentService._reconcile_order_payment(session, order)
        await session.flush()
        session.add(PaymentWebhookEvent(payment_id=manual_payment.id, provider=payload.payment_method, request_id=f"manual-{uuid.uuid4().hex}", event_status="SUCCESS", payload=confirmation))
        await PaymentService._notify_payment_status(session, order, manual_payment, admin_user_id)
        await session.commit()
        await session.refresh(manual_payment)
        return order, manual_payment

    @staticmethod
    async def _main_order_for_registration(session: AsyncSession, registration: Registration) -> Order:
        from app.modules.iwbif.models import DelegateRegistrationPackageSelection
        from app.modules.participants.models import ParticipantProfile
        from app.modules.store.models import OrderItem

        order = (await session.execute(
            select(Order)
            .join(OrderItem, OrderItem.order_id == Order.id)
            .where(
                Order.registration_id == registration.id,
                OrderItem.product_type == "delegate",
            )
            .order_by(Order.created_at.desc())
            .with_for_update()
        )).scalars().first()
        if order:
            return order

        main = (await session.execute(
            select(DelegateRegistrationPackageSelection)
            .where(
                DelegateRegistrationPackageSelection.registration_id == registration.id,
                DelegateRegistrationPackageSelection.selection_role == "main",
            )
            .with_for_update()
        )).scalar_one_or_none()
        if not main:
            raise ValidationException("MAIN_PACKAGE_SELECTION_REQUIRED", "Registrasi belum memiliki pilihan main package")
        owner_id = (await session.execute(
            select(ParticipantProfile.user_id).where(ParticipantProfile.id == registration.participant_id)
        )).scalar_one_or_none()
        if not owner_id:
            raise ValidationException("REGISTRATION_OWNER_NOT_FOUND", "Pemilik registrasi tidak ditemukan")
        order = Order(
            user_id=owner_id,
            registration_id=registration.id,
            event_id=registration.event_id,
            order_number=f"ORD-{uuid.uuid4().hex[:16].upper()}",
            order_kind=OrderKind.MAIN_REGISTRATION,
            subtotal=main.selected_payment_amount,
            discount_amount=0,
            tax_amount=0,
            service_fee=0,
            total_amount=main.selected_payment_amount,
            currency=main.payment_currency,
            status=OrderStatus.PENDING,
        )
        session.add(order)
        await session.flush()
        session.add(OrderItem(
            order_id=order.id,
            product_id=None,
            product_code=f"{main.package_code}_{main.occupancy_type}"[:60],
            product_name=f"{main.package_name} - {main.rate_name}",
            product_type="delegate",
            quantity=1,
            unit_price=main.selected_payment_amount,
            currency=main.payment_currency,
            line_total=main.selected_payment_amount,
            metadata_json={
                "delegate_package_id": str(main.delegate_package_id),
                "delegate_package_rate_id": str(main.package_rate_id),
                "package_type": "main",
                "package_code": main.package_code,
                "package_name": main.package_name,
                "rate_name": main.rate_name,
                "occupancy_type": main.occupancy_type,
                "display_amount": str(main.selected_amount),
                "display_currency": main.selected_currency,
            },
        ))
        await session.flush()
        return order

    @staticmethod
    async def create_offline_registration_payment(
        session: AsyncSession,
        registration_id: uuid.UUID,
        payload: schemas.OfflineRegistrationPaymentRequest,
        admin_user_id: uuid.UUID,
    ):
        from app.modules.tickets.repository import TicketRepository

        registration = await session.get(Registration, registration_id, with_for_update=True)
        if not registration or registration.status in {RegistrationStatus.CANCELED, RegistrationStatus.CANCELLED}:
            raise NotFoundException("REGISTRATION_NOT_FOUND", "Registrasi aktif tidak ditemukan")
        receipt = payload.receipt_number.strip().upper()
        existing = (await session.execute(
            select(Payment).where(Payment.offline_receipt_number == receipt).with_for_update()
        )).scalar_one_or_none()
        if existing:
            order = await session.get(Order, existing.order_id)
            if not order or order.registration_id != registration.id:
                raise ConflictException("OFFLINE_RECEIPT_ALREADY_USED", "Nomor kuitansi sudah digunakan pada registrasi lain")
            ticket = await TicketRepository.get_by_registration(session, registration.id)
            if existing.transaction_status == PaymentStatus.SUCCESS and order.status == OrderStatus.PAID and not ticket:
                ticket = await TicketRepository.issue(session, registration.id)
            return order, existing, ticket

        order = await PaymentService._main_order_for_registration(session, registration)
        if order.currency.upper() != payload.currency:
            raise ValidationException("OFFLINE_PAYMENT_CURRENCY_MISMATCH", "Mata uang pembayaran offline tidak sesuai order")
        paid_amount, remaining_amount = await PaymentService._payment_progress(session, order)
        if remaining_amount == 0:
            raise ConflictException("ORDER_ALREADY_PAID", "Main order sudah lunas")
        amount = Decimal(str(payload.amount if payload.amount is not None else remaining_amount)).quantize(Decimal("0.01"))
        if amount != remaining_amount:
            raise ValidationException(
                "OFFLINE_PAYMENT_MUST_SETTLE_REMAINDER",
                f"Pembayaran offline harus tepat sebesar sisa tagihan {remaining_amount}",
            )
        paid_at = payload.paid_at or datetime.now(timezone.utc)
        audit = {
            "confirmed_by": str(admin_user_id),
            "registration_id": str(registration.id),
            "payment_method": payload.payment_method,
            "receipt_number": receipt,
            "amount": str(amount),
            "currency": payload.currency,
            "notes": payload.notes,
            "confirmed_at": paid_at.isoformat(),
            "previous_paid_amount": str(paid_amount),
        }
        payment = Payment(
            order_id=order.id,
            provider=payload.payment_method,
            provider_order_id=receipt,
            provider_transaction_id=receipt,
            provider_reference_no=receipt,
            offline_receipt_number=receipt,
            confirmed_by=admin_user_id,
            payment_type="offline",
            channel_code=payload.payment_method.upper(),
            gross_amount=amount,
            currency=payload.currency,
            transaction_status=PaymentStatus.SUCCESS,
            paid_at=paid_at,
            raw_response=json.dumps(audit),
        )
        session.add(payment)
        await session.flush()
        _, remaining_after, _ = await PaymentService._reconcile_order_payment(session, order)
        if remaining_after != 0 or order.status != OrderStatus.PAID:
            raise ValidationException("OFFLINE_PAYMENT_RECONCILIATION_FAILED", "Pembayaran offline belum melunasi main order")
        session.add(PaymentWebhookEvent(
            payment_id=payment.id,
            provider=payload.payment_method,
            request_id=f"offline-{uuid.uuid4().hex}",
            event_status="SUCCESS",
            payload=audit,
        ))
        await PaymentService._notify_payment_status(session, order, payment, admin_user_id)
        try:
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            raise ConflictException("OFFLINE_RECEIPT_ALREADY_USED", "Nomor kuitansi sudah digunakan") from exc
        await session.refresh(payment)
        ticket = await TicketRepository.get_by_registration(session, registration.id)
        if not ticket:
            ticket = await TicketRepository.issue(session, registration.id)
        return order, payment, ticket

    @staticmethod
    async def update_transaction_status(
        session: AsyncSession,
        payment_id: uuid.UUID,
        payload: schemas.TransactionStatusUpdateRequest,
        actor_user_id: uuid.UUID,
        *,
        commit: bool = True,
    ) -> tuple[Order, Payment]:
        payment = await session.get(Payment, payment_id, with_for_update=True)
        if not payment:
            raise NotFoundException("PAYMENT_NOT_FOUND", "Transaksi pembayaran tidak ditemukan")
        if payment.deleted_at:
            raise ConflictException("PAYMENT_DELETED", "Transaksi yang sudah dihapus tidak dapat diubah")
        order = await session.get(Order, payment.order_id, with_for_update=True)
        if not order:
            raise NotFoundException("ORDER_NOT_FOUND", "Order transaksi tidak ditemukan")

        now = datetime.now(timezone.utc)
        requested_status = payload.status.lower()
        current_status = payment.transaction_status
        allowed_actions = payment_allowed_actions(current_status, payment.deleted_at)
        if requested_status in {"paid", "success"}:
            if requested_status not in allowed_actions:
                raise ConflictException(
                    "INVALID_PAYMENT_STATUS_TRANSITION",
                    f"Transaksi berstatus {current_status} tidak dapat diubah menjadi success",
                )
            payment.transaction_status = PaymentStatus.SUCCESS
            payment.paid_at = payload.paid_at or payment.paid_at or now
            await PaymentService._reconcile_order_payment(session, order)
            event_status = "SUCCESS"
        else:
            if requested_status not in allowed_actions:
                raise ConflictException(
                    "INVALID_PAYMENT_STATUS_TRANSITION",
                    f"Transaksi berstatus {current_status} tidak dapat dibatalkan",
                )
            payment.transaction_status = PaymentStatus.CANCELED
            payment.paid_at = None
            paid, _, _ = await PaymentService._reconcile_order_payment(session, order)
            if paid == 0:
                order.status = OrderStatus.CANCELED
                order.canceled_at = now
                order.canceled_by = actor_user_id
                order.cancellation_reason = payload.notes
            event_status = "CANCELED"

        audit_payload = {
            "updated_by": str(actor_user_id),
            "requested_status": requested_status,
            "transaction_status": payment.transaction_status,
            "notes": payload.notes,
            "updated_at": now.isoformat(),
        }
        session.add(PaymentWebhookEvent(
            payment_id=payment.id,
            provider=payment.provider,
            request_id=f"organizer-{uuid.uuid4().hex}",
            event_status=event_status,
            payload=audit_payload,
        ))
        await PaymentService._notify_payment_status(session, order, payment, actor_user_id)
        if commit:
            await session.commit()
            await session.refresh(payment)
        else:
            await session.flush()
        return order, payment

    @staticmethod
    async def delete_transaction(
        session: AsyncSession,
        payment_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        reason: str | None = None,
        *,
        commit: bool = True,
    ) -> tuple[Order, Payment]:
        payment = await session.get(Payment, payment_id, with_for_update=True)
        if not payment:
            raise NotFoundException("PAYMENT_NOT_FOUND", "Transaksi pembayaran tidak ditemukan")
        if payment.deleted_at:
            raise ConflictException("PAYMENT_ALREADY_DELETED", "Transaksi pembayaran sudah dihapus")
        if "delete" not in payment_allowed_actions(payment.transaction_status, payment.deleted_at):
            raise ConflictException(
                "PAYMENT_DELETE_FORBIDDEN",
                "Transaksi success atau refunded tidak boleh dihapus; pertahankan sebagai catatan keuangan",
            )
        order_id = payment.order_id
        order = await session.get(Order, order_id, with_for_update=True)
        if not order:
            raise NotFoundException("ORDER_NOT_FOUND", "Order transaksi tidak ditemukan")

        now = datetime.now(timezone.utc)
        payment.deleted_at = now
        payment.deleted_by = actor_user_id
        payment.deletion_reason = reason
        session.add(PaymentWebhookEvent(
            payment_id=payment.id,
            provider=payment.provider,
            request_id=f"organizer-delete-{uuid.uuid4().hex}",
            event_status="SOFT_DELETED",
            payload={
                "deleted_by": str(actor_user_id),
                "reason": reason,
                "deleted_at": now.isoformat(),
            },
        ))

        await PaymentService._reconcile_order_payment(session, order)
        if commit:
            await session.commit()
            await session.refresh(payment)
        else:
            await session.flush()
        return order, payment

    @staticmethod
    async def bulk_transaction_action(
        session: AsyncSession,
        payload: schemas.TransactionBulkActionRequest,
        actor_user_id: uuid.UUID,
    ) -> list[tuple[Order, Payment]]:
        results: list[tuple[Order, Payment]] = []
        try:
            for payment_id in payload.payment_ids:
                if payload.action == "delete":
                    result = await PaymentService.delete_transaction(
                        session, payment_id, actor_user_id, payload.notes, commit=False
                    )
                else:
                    result = await PaymentService.update_transaction_status(
                        session,
                        payment_id,
                        schemas.TransactionStatusUpdateRequest(
                            status=payload.action, notes=payload.notes, paid_at=payload.paid_at
                        ),
                        actor_user_id,
                        commit=False,
                    )
                results.append(result)
            await session.commit()
            for _, payment in results:
                await session.refresh(payment)
            return results
        except Exception:
            await session.rollback()
            raise

    @staticmethod
    async def create_doku_qris(session: AsyncSession, payload: schemas.CreateDokuQrisRequest, user_id: uuid.UUID) -> schemas.DokuQrisResponse:
        registrations = await PaymentRepository.get_registrations_for_user(session, user_id)
        registration = next((row for row in registrations if row.id == payload.registration_id), None)
        if not registration:
            raise NotFoundException("REGISTRATION_NOT_FOUND", "Registrasi tidak ditemukan untuk akun ini")
        order = await PaymentRepository.get_latest_order(session, registration.id)
        if order and order.status == OrderStatus.PAID:
            raise ConflictException("ORDER_ALREADY_PAID", "Registrasi ini sudah dibayar")
        if not order or order.status not in {OrderStatus.PENDING, OrderStatus.PARTIALLY_PAID, OrderStatus.DRAFT}:
            order = await PaymentRepository.create_order(session, registration.id)
        if order.currency.upper() != "IDR":
            raise ValidationException("DOKU_IDR_REQUIRED", "QRIS hanya menerima tagihan IDR")
        sequence, sequence_count, segment_amount = await PaymentService._next_payment_segment(session, order)
        payment = (await session.execute(select(Payment).where(
            Payment.order_id == order.id,
            Payment.provider == "doku_snap_qris",
            Payment.payment_sequence == sequence,
            Payment.deleted_at.is_(None),
        ).order_by(Payment.created_at.desc()))).scalars().first()
        if payment and payment.transaction_status in {PaymentStatus.CREATED, PaymentStatus.PENDING} and payment.payment_instructions_url:
            paid, remaining = await PaymentService._payment_progress(session, order)
            return schemas.DokuQrisResponse(payment_id=payment.id, order_id=order.id, order_number=order.order_number, status=payment.transaction_status, qr_content=payment.payment_instructions_url, amount=float(payment.gross_amount), currency=order.currency, expires_at=order.expires_at, payment_sequence=sequence, payment_sequence_count=sequence_count, paid_amount=float(paid), remaining_amount=float(remaining))
        reference = f"QR{uuid.uuid4().hex[:20].upper()}"
        body = {"partnerReferenceNo": reference, "amount": {"value": str(segment_amount), "currency": "IDR"}, "additionalInfo": {"feeType": "1", "orderId": str(order.id), "paymentSequence": sequence}}
        if order.expires_at:
            body["validityPeriod"] = order.expires_at.astimezone().isoformat(timespec="seconds")
        response, external_id = await DokuSnapClient().create_qris(body)
        qr_content = str(response.get("qrContent") or "")
        if not qr_content:
            raise ValidationException("DOKU_QRIS_CONTENT_MISSING", "DOKU tidak mengembalikan konten QRIS")
        if not payment:
            payment = Payment(order_id=order.id, provider="doku_snap_qris", gross_amount=segment_amount, currency=order.currency, payment_sequence=sequence, payment_sequence_count=sequence_count)
            session.add(payment)
        payment.provider, payment.provider_order_id, payment.payment_type, payment.channel_code = "doku_snap_qris", reference, "doku_snap_qris", "QRIS"
        payment.external_id, payment.provider_reference_no, payment.payment_instructions_url = external_id, response.get("referenceNo"), qr_content
        payment.raw_response, payment.transaction_status = json.dumps(response), PaymentStatus.PENDING
        await session.commit()
        await session.refresh(payment)
        paid, remaining = await PaymentService._payment_progress(session, order)
        return schemas.DokuQrisResponse(payment_id=payment.id, order_id=order.id, order_number=order.order_number, status=payment.transaction_status, qr_content=qr_content, amount=float(payment.gross_amount), currency=order.currency, expires_at=order.expires_at, payment_sequence=sequence, payment_sequence_count=sequence_count, paid_amount=float(paid), remaining_amount=float(remaining))

    @staticmethod
    async def create_doku_direct_debit_binding(session: AsyncSession, payload: schemas.CreateDirectDebitBindingRequest, user_id: uuid.UUID) -> schemas.DirectDebitBindingResponse:
        registrations = await PaymentRepository.get_registrations_for_user(session, user_id)
        registration = next((row for row in registrations if row.id == payload.registration_id), None)
        if not registration:
            raise NotFoundException("REGISTRATION_NOT_FOUND", "Registrasi tidak ditemukan untuk akun ini")
        channel_code = payload.channel_code.strip().upper()
        client = DokuSnapClient()
        channel = client.direct_debit_channels().get(channel_code)
        if not channel:
            raise ValidationException("DOKU_DIRECT_DEBIT_CHANNEL_NOT_CONFIGURED", f"Direct Debit {channel_code} belum dikonfigurasi")
        customer_reference = f"B{uuid.uuid4().hex[:11].upper()}"
        body: dict[str, Any] = {
            "partnerReferenceNo": customer_reference,
            "phoneNo": payload.phone_no,
            "additionalInfo": {
                "channel": channel.get("channel") or f"DIRECT_DEBIT_{channel_code}_SNAP",
                "custIdMerchant": str(registration.participant_id),
            },
        }
        if payload.device_id:
            body["deviceId"] = payload.device_id
        response, _external_id = await client.direct_debit_request(channel_code, "/direct-debit/core/v1/registration-account-binding", body)
        additional = response.get("additionalInfo") or {}
        token = additional.get("bankCardToken") or additional.get("tokenId")
        binding = DirectDebitBinding(
            participant_id=registration.participant_id,
            channel_code=channel_code,
            customer_reference=customer_reference,
            provider_reference_no=response.get("referenceNo"),
            token_id=token,
            status=str(additional.get("status") or "pending").lower(),
            raw_response=json.dumps(response),
        )
        session.add(binding)
        await session.commit()
        await session.refresh(binding)
        return schemas.DirectDebitBindingResponse(binding_id=binding.id, channel_code=channel_code, status=binding.status, redirect_url=response.get("redirectUrl") or response.get("webRedirectUrl"))

    @staticmethod
    async def create_doku_direct_debit_payment(session: AsyncSession, payload: schemas.CreateDirectDebitPaymentRequest, user_id: uuid.UUID) -> schemas.DirectDebitPaymentResponse:
        bindings = await PaymentRepository.get_direct_debit_binding_for_user(session, payload.binding_id, user_id)
        if not bindings or not bindings.token_id:
            raise ValidationException("DOKU_DIRECT_DEBIT_BINDING_REQUIRED", "Binding Direct Debit aktif tidak ditemukan")
        registrations = await PaymentRepository.get_registrations_for_user(session, user_id)
        registration = next((row for row in registrations if row.id == payload.registration_id and row.participant_id == bindings.participant_id), None)
        if not registration:
            raise NotFoundException("REGISTRATION_NOT_FOUND", "Registrasi tidak ditemukan untuk binding ini")
        order = await PaymentRepository.get_latest_order(session, registration.id)
        if order and order.status == OrderStatus.PAID:
            raise ConflictException("ORDER_ALREADY_PAID", "Registrasi ini sudah dibayar")
        if not order or order.status not in {OrderStatus.PENDING, OrderStatus.DRAFT}:
            order = await PaymentRepository.create_order(session, registration.id)
        if order.currency.upper() != "IDR":
            raise ValidationException("DOKU_IDR_REQUIRED", "Direct Debit hanya menerima tagihan IDR")
        payment = await PaymentRepository.get_payment_by_order(session, order.id)
        if payment and payment.provider not in {"doku", "doku_snap_direct_debit"}:
            raise ConflictException("LEGACY_PAYMENT_PENDING", "Batalkan transaksi gateway lama terlebih dahulu")
        reference = f"DD{uuid.uuid4().hex[:10].upper()}"
        if not payment:
            from app.modules.payments.models import Payment
            payment = Payment(order_id=order.id, provider="doku_snap_direct_debit", provider_order_id=reference, payment_type="doku_snap_direct_debit", channel_code=bindings.channel_code, gross_amount=order.total_amount, currency=order.currency, transaction_status=PaymentStatus.PENDING)
            session.add(payment)
        else:
            payment.provider, payment.provider_order_id, payment.payment_type, payment.channel_code, payment.transaction_status = "doku_snap_direct_debit", reference, "doku_snap_direct_debit", bindings.channel_code, PaymentStatus.PENDING
        await session.flush()
        channel = DokuSnapClient().direct_debit_channels().get(bindings.channel_code)
        body = {"partnerReferenceNo": reference, "amount": {"value": str(order.total_amount), "currency": "IDR"}, "additionalInfo": {"channel": (channel or {}).get("channel") or f"DIRECT_DEBIT_{bindings.channel_code}_SNAP", "remarks": f"IWBIF {registration.registration_number}"}}
        response, external_id = await DokuSnapClient().direct_debit_request(bindings.channel_code, "/direct-debit/core/v1/debit/payment-host-to-host", body, customer_token=bindings.token_id)
        payment.external_id, payment.provider_reference_no, payment.checkout_url, payment.raw_response = external_id, response.get("referenceNo"), response.get("webRedirectUrl") or response.get("redirectUrl"), json.dumps(response)
        await session.commit()
        return schemas.DirectDebitPaymentResponse(payment_id=payment.id, order_id=order.id, partner_reference_no=reference, status=payment.transaction_status, redirect_url=payment.checkout_url)

    @staticmethod
    async def verify_doku_direct_debit_otp(session: AsyncSession, payment_id: uuid.UUID, payload: schemas.VerifyDirectDebitOtpRequest, user_id: uuid.UUID) -> dict[str, Any]:
        payment = await PaymentRepository.get_payment_for_user(session, payment_id, user_id)
        binding = await PaymentRepository.get_direct_debit_binding_for_user(session, payload.binding_id, user_id)
        if not payment or payment.provider != "doku_snap_direct_debit" or not binding or not binding.token_id:
            raise NotFoundException("DOKU_DIRECT_DEBIT_PAYMENT_NOT_FOUND", "Pembayaran atau binding Direct Debit tidak ditemukan")
        if payment.channel_code != binding.channel_code or not payment.provider_order_id:
            raise ValidationException("DOKU_DIRECT_DEBIT_BINDING_MISMATCH", "Binding tidak sesuai dengan pembayaran")
        if not payload.otp.isdigit() or len(payload.otp) != 6:
            raise ValidationException("DOKU_DIRECT_DEBIT_INVALID_OTP", "OTP harus terdiri dari 6 digit")
        channel = DokuSnapClient().direct_debit_channels().get(binding.channel_code) or {}
        body = {"originalPartnerReferenceNo": payment.provider_order_id, "otp": payload.otp, "action": "otpPayment", "additionalInfo": {"channel": channel.get("channel") or f"DIRECT_DEBIT_{binding.channel_code}_SNAP", "bankCardToken": binding.token_id}}
        response, external_id = await DokuSnapClient().direct_debit_request(binding.channel_code, "/direct-debit/core/v1/otp-verification", body, customer_token=binding.token_id)
        payment.external_id, payment.raw_response = external_id, json.dumps(response)
        await session.commit()
        return response
    @staticmethod
    async def create_doku_direct_va(session: AsyncSession, payload: schemas.CreateDokuDirectVARequest, user_id: uuid.UUID) -> schemas.DokuDirectVAResponse:
        registrations = await PaymentRepository.get_registrations_for_user(session, user_id)
        registration = next((row for row in registrations if row.id == payload.registration_id), None)
        if not registration:
            raise NotFoundException("REGISTRATION_NOT_FOUND", "Registrasi tidak ditemukan untuk akun ini")
        order = await PaymentRepository.get_latest_order(session, registration.id)
        if order and order.status == OrderStatus.PAID:
            raise ConflictException("ORDER_ALREADY_PAID", "Registrasi ini sudah dibayar")
        if not order or order.status not in {OrderStatus.PENDING, OrderStatus.DRAFT}:
            order = await PaymentRepository.create_order(session, registration.id)
        if order.currency.upper() != "IDR":
            raise ValidationException("DOKU_IDR_REQUIRED", "DOKU Direct VA hanya menerima tagihan IDR; atur harga pembayaran paket dalam IDR")
        payment = await PaymentRepository.get_payment_by_order(session, order.id)
        bank_code = payload.bank_code.strip().upper()
        if payment and payment.payment_type == "doku_snap_va" and payment.channel_code == bank_code and payment.virtual_account_no:
            return schemas.DokuDirectVAResponse(payment_id=payment.id, order_id=order.id, order_number=order.order_number, status=payment.transaction_status, bank_code=bank_code, virtual_account_no=payment.virtual_account_no, amount=float(payment.gross_amount), currency=payment.currency, expires_at=payment.expired_at, instructions_url=payment.payment_instructions_url)
        if payment and payment.transaction_status in {PaymentStatus.CREATED, PaymentStatus.PENDING}:
            raise ConflictException("PAYMENT_CHANNEL_ALREADY_SELECTED", "Order memiliki transaksi aktif; gunakan atau batalkan transaksi tersebut")
        if not payment:
            payment = await PaymentRepository.create_doku_payment(session, order)

        participant = await session.get(ParticipantProfile, registration.participant_id)
        user = await session.get(User, participant.user_id) if participant else None
        if not participant or not user:
            raise ValidationException("PAYMENT_DATA_INCOMPLETE", "Data peserta pembayaran tidak lengkap")
        amount = Decimal(str(order.total_amount)).quantize(Decimal("0.01"))
        expires_at = order.expires_at or datetime.now(timezone.utc)
        request_payload = {
            "customerNo": "0",
            "virtualAccountNo": "",
            "virtualAccountName": participant.full_name[:255],
            "virtualAccountEmail": user.email,
            "virtualAccountPhone": user.phone or "",
            "trxId": order.order_number,
            "totalAmount": {"value": f"{amount:.2f}", "currency": "IDR"},
            "virtualAccountTrxType": "C",
            "expiredDate": expires_at.astimezone().isoformat(timespec="seconds"),
            "additionalInfo": {"channel": f"VIRTUAL_ACCOUNT_{bank_code}"},
        }
        response, external_id = await DokuSnapClient().create_va(bank_code, request_payload)
        va = response.get("virtualAccountData", {})
        va_no = str(va.get("virtualAccountNo") or "").strip()
        if not va_no:
            raise ValidationException("DOKU_VA_NUMBER_MISSING", "DOKU tidak mengembalikan nomor Virtual Account")
        additional = va.get("additionalInfo") or {}
        payment.provider = "doku"
        payment.payment_type = "doku_snap_va"
        payment.channel_code = bank_code
        payment.virtual_account_no = va_no
        payment.provider_transaction_id = external_id
        payment.provider_reference_no = str(va.get("trxId") or response.get("referenceNo") or "") or None
        payment.provider_order_id = order.order_number
        payment.external_id = external_id
        payment.payment_instructions_url = additional.get("howToPayPage")
        payment.raw_response = json.dumps(response)
        payment.transaction_status = PaymentStatus.PENDING
        payment.expired_at = order.expires_at
        order.status = OrderStatus.PENDING
        await session.commit()
        await session.refresh(payment)
        return schemas.DokuDirectVAResponse(payment_id=payment.id, order_id=order.id, order_number=order.order_number, status=payment.transaction_status, bank_code=bank_code, virtual_account_no=va_no, amount=float(payment.gross_amount), currency=payment.currency, expires_at=payment.expired_at, instructions_url=payment.payment_instructions_url)

    @staticmethod
    def issue_doku_snap_token(body: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
        settings = get_settings()
        client_key = headers.get("x-client-key", "")
        timestamp = headers.get("x-timestamp", "")
        signature = headers.get("x-signature", "")
        expected_client = settings.DOKU_SNAP_DOKU_CLIENT_ID
        if body.get("grantType") != "client_credentials" or not all((client_key, timestamp, signature)):
            raise ValidationException("DOKU_SNAP_INVALID_TOKEN_REQUEST", "Permintaan token B2B tidak valid")
        if expected_client and client_key != expected_client:
            raise ValidationException("DOKU_SNAP_UNKNOWN_CLIENT", "X-CLIENT-KEY DOKU tidak dikenal")
        ensure_fresh_timestamp(timestamp)
        if not verify_asymmetric_signature(signature, client_key, timestamp, settings.DOKU_SNAP_DOKU_PUBLIC_KEY_PATH):
            raise ValidationException("DOKU_SNAP_INVALID_SIGNATURE", "Signature token B2B DOKU tidak valid")
        token, ttl = issue_merchant_token(client_key)
        return {"responseCode": "2007300", "responseMessage": "Successful", "accessToken": token, "tokenType": "Bearer", "expiresIn": ttl, "additionalInfo": {}}

    @staticmethod
    async def handle_doku_snap_va_notification(session: AsyncSession, payload: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
        settings = get_settings()
        timestamp = headers.get("x-timestamp", "")
        signature = headers.get("x-signature", "")
        external_id = headers.get("x-external-id", "")
        authorization = headers.get("authorization", "")
        partner_id = headers.get("x-partner-id", "")
        if not all((timestamp, signature, external_id, authorization, partner_id)) or not authorization.startswith("Bearer "):
            raise ValidationException("DOKU_SNAP_INVALID_HEADERS", "Header notification SNAP tidak lengkap")
        if settings.DOKU_SNAP_DOKU_CLIENT_ID and partner_id != settings.DOKU_SNAP_DOKU_CLIENT_ID:
            raise ValidationException("DOKU_SNAP_UNKNOWN_PARTNER", "X-PARTNER-ID notification tidak dikenal")
        ensure_fresh_timestamp(timestamp)
        token = authorization[7:]
        if not verify_merchant_token(token):
            raise ValidationException("DOKU_SNAP_INVALID_TOKEN", "Bearer token notification tidak valid")
        if not verify_symmetric_signature(signature, "POST", settings.DOKU_SNAP_VA_NOTIFICATION_PATH, token, payload, timestamp, settings.DOKU_SNAP_CLIENT_SECRET):
            raise ValidationException("DOKU_SNAP_INVALID_SIGNATURE", "X-SIGNATURE notification tidak valid")

        va_no = str(payload.get("virtualAccountNo") or "").strip()
        trx_id = str(payload.get("trxId") or "")
        paid = payload.get("paidAmount") or {}
        if not va_no or not trx_id or paid.get("value") is None or paid.get("currency") != "IDR":
            raise ValidationException("DOKU_SNAP_INVALID_PAYLOAD", "Payload pembayaran VA SNAP tidak valid")
        payment = await PaymentRepository.get_payment_by_va(session, va_no, lock=True)
        if not payment or payment.provider_order_id != trx_id:
            raise NotFoundException("DOKU_SNAP_PAYMENT_NOT_FOUND", "Transaksi Virtual Account tidak ditemukan")
        order = await session.get(Order, payment.order_id, with_for_update=True)
        if not order:
            raise NotFoundException("DOKU_ORDER_NOT_FOUND", "Order tidak ditemukan")
        if Decimal(str(paid["value"])) != Decimal(str(payment.gross_amount)):
            raise ValidationException("DOKU_AMOUNT_MISMATCH", "Nominal pembayaran tidak sesuai order")
        provider = "doku_snap_va"
        duplicate = await PaymentRepository.get_webhook_event(session, external_id, provider)
        if not duplicate:
            payment.transaction_status = PaymentStatus.SUCCESS
            payment.paid_at = datetime.now(timezone.utc)
            payment.raw_response = json.dumps(payload)
            _, remaining, became_paid = await PaymentService._reconcile_order_payment(session, order)
            await PaymentService._notify_payment_status(session, order, payment)
            session.add(PaymentWebhookEvent(payment_id=payment.id, provider=provider, request_id=external_id, event_status="SUCCESS", payload=payload))
            await session.commit()
            if became_paid and remaining == 0:
                asyncio.create_task(deliver_payment_for_order(order.id))
        return {"responseCode": "2002500", "responseMessage": "Successful", "virtualAccountData": {"partnerServiceId": payload.get("partnerServiceId"), "customerNo": payload.get("customerNo"), "virtualAccountNo": payload.get("virtualAccountNo"), "virtualAccountName": payload.get("virtualAccountName"), "trxId": payload.get("trxId"), "paymentRequestId": payload.get("paymentRequestId"), "paidAmount": payload.get("paidAmount")}}

    @staticmethod
    async def handle_doku_snap_direct_debit_notification(session: AsyncSession, payload: dict[str, Any], headers: dict[str, str], *, e_wallet: bool = False) -> dict[str, Any]:
        settings = get_settings()
        timestamp, signature = headers.get("x-timestamp", ""), headers.get("x-signature", "")
        external_id, authorization = headers.get("x-external-id", ""), headers.get("authorization", "")
        if not all((timestamp, signature, external_id, authorization)) or not authorization.startswith("Bearer "):
            raise ValidationException("DOKU_SNAP_INVALID_HEADERS", "Header notification Direct Debit tidak lengkap")
        ensure_fresh_timestamp(timestamp)
        token = authorization[7:]
        if not verify_merchant_token(token):
            raise ValidationException("DOKU_SNAP_INVALID_TOKEN", "Bearer token notification tidak valid")
        additional = payload.get("additionalInfo") or {}
        channel_value = str(additional.get("channel") or payload.get("channel") or "").upper()
        client = DokuSnapClient()
        channels = client.e_wallet_channels() if e_wallet else client.direct_debit_channels()
        channel_key, config = next(((key, item) for key, item in channels.items() if str(item.get("channel") or "").upper() == channel_value), (None, None))
        if not config:
            raise ValidationException("DOKU_DIRECT_DEBIT_UNKNOWN_CHANNEL", "Channel Direct Debit notification tidak dikonfigurasi")
        secret = str(config.get("consumer_secret") or settings.DOKU_SNAP_CLIENT_SECRET)
        notification_path = settings.DOKU_SNAP_EWALLET_NOTIFICATION_PATH if e_wallet else settings.DOKU_SNAP_DIRECT_DEBIT_NOTIFICATION_PATH
        if not secret or not verify_symmetric_signature(signature, "POST", notification_path, token, payload, timestamp, secret):
            raise ValidationException("DOKU_SNAP_INVALID_SIGNATURE", "Signature notification pembayaran tidak valid")
        reference = str(payload.get("partnerReferenceNo") or payload.get("originalPartnerReferenceNo") or "")
        if not reference:
            raise ValidationException("DOKU_SNAP_INVALID_PAYLOAD", "partnerReferenceNo wajib ada")
        payment = await PaymentRepository.get_payment_by_provider_order_id(session, reference, lock=True)
        if not payment:
            raise NotFoundException("DOKU_SNAP_PAYMENT_NOT_FOUND", "Pembayaran Direct Debit tidak ditemukan")
        order = await session.get(Order, payment.order_id, with_for_update=True)
        if not order:
            raise NotFoundException("DOKU_ORDER_NOT_FOUND", "Order tidak ditemukan")
        amount = payload.get("amount") or payload.get("paidAmount") or {}
        if amount.get("value") is not None and Decimal(str(amount["value"])) != Decimal(str(order.total_amount)):
            raise ValidationException("DOKU_AMOUNT_MISMATCH", "Nominal pembayaran Direct Debit tidak sesuai order")
        status = str(payload.get("latestTransactionStatus") or payload.get("transactionStatus") or payload.get("status") or "SUCCESS").upper()
        provider = "doku_snap_e_wallet" if e_wallet else "doku_snap_direct_debit"
        if not await PaymentRepository.get_webhook_event(session, external_id, provider):
            notify_paid = status in {"SUCCESS", "PAID", "00"}
            if status in {"SUCCESS", "PAID", "00"}:
                payment.transaction_status, payment.paid_at = PaymentStatus.SUCCESS, datetime.now(timezone.utc)
            elif status in {"FAILED", "CANCELLED", "CANCELED"}:
                payment.transaction_status = PaymentStatus.FAILED
            _, remaining, became_paid = await PaymentService._reconcile_order_payment(session, order)
            await PaymentService._notify_payment_status(session, order, payment)
            payment.channel_code = channel_value or channel_key
            payment.raw_response = json.dumps(payload)
            session.add(PaymentWebhookEvent(payment_id=payment.id, provider=provider, request_id=external_id, event_status=status, payload=payload))
            await session.commit()
            if notify_paid and became_paid and remaining == 0:
                asyncio.create_task(deliver_payment_for_order(order.id))
        return {"responseCode": "2005400", "responseMessage": "Successful", "partnerReferenceNo": reference, "additionalInfo": {}}
    @staticmethod
    def _is_doku_checkout(payment: Payment) -> bool:
        # Null identifies hosted checkouts created before the explicit marker.
        return (payment.provider == "doku" and not payment.virtual_account_no
                and payment.payment_type in {None, "doku_checkout", "CREDIT_CARD"})

    @staticmethod
    async def _next_doku_checkout_segment(session: AsyncSession, order: Order, payments: list[Payment]) -> tuple[int, int, Decimal]:
        _, remaining = await PaymentService._payment_progress(session, order)
        configured_limit = Decimal(str(get_settings().QRIS_SEGMENT_LIMIT_IDR))
        if not configured_limit.is_finite() or configured_limit <= 0:
            raise ValidationException("INVALID_PAYMENT_LIMIT", "Batas pembayaran tidak valid")
        limit = min(configured_limit, Decimal("9000000"))
        if not remaining.is_finite() or remaining <= 0:
            raise ConflictException("ORDER_ALREADY_PAID", "Order sudah lunas")
        if remaining != remaining.to_integral_value() or limit != limit.to_integral_value():
            raise ValidationException("DOKU_INTEGER_AMOUNT_REQUIRED", "Nominal DOKU harus berupa Rupiah bulat")
        # Legacy payments can have different amounts or no sequence. Use settled
        # money, not the original plan's indexes, to avoid overcharging the balance.
        sequence = max((item.payment_sequence or 0 for item in payments
                        if item.transaction_status == PaymentStatus.SUCCESS), default=0) + 1
        count = sequence - 1 + int((remaining + limit - 1) // limit)
        return sequence, count, min(limit, remaining)

    @staticmethod
    async def create_doku_checkout(
        session: AsyncSession,
        payload: schemas.CreateDokuCheckoutRequest,
        user_id: uuid.UUID,
    ) -> tuple[schemas.DokuCheckoutResponse, Order]:
        registrations = await PaymentRepository.get_registrations_for_user(session, user_id)
        registration = None
        if payload.order_id is not None:
            latest_order = await PaymentRepository.get_order_for_user(session, payload.order_id, user_id, lock=True)
            if latest_order is None:
                raise NotFoundException("ORDER_NOT_FOUND", "Order tidak ditemukan untuk akun ini")
            await PaymentService._reject_direct_gateway_for_hari_santri(session, latest_order)
            if latest_order.status == OrderStatus.PAID:
                latest_payment = await PaymentRepository.get_payment_by_order(session, latest_order.id)
                return schemas.DokuCheckoutResponse(payment_url="", already_paid=True, payment_id=latest_payment.id if latest_payment else None, order_status=latest_order.status, requires_payment=False), latest_order
            if latest_order.status not in {OrderStatus.PENDING, OrderStatus.PARTIALLY_PAID, OrderStatus.DRAFT}:
                raise ConflictException("ORDER_NOT_PAYABLE", "Order tidak dapat dibayar")
            payment = None
        else:
            latest_order = None
            payment = None
        if payload.registration_id is not None:
            registration = next(
                (reg for reg in registrations if reg.id == payload.registration_id),
                None,
            )
            if registration is None:
                raise ValidationException(
                    code="REGISTRATION_NOT_OWNED",
                    message="Registrasi tidak ditemukan untuk akun ini",
                )
        elif payload.order_id is None:
            payable = [
                reg for reg in registrations
                if getattr(reg.status, "value", reg.status) in {"awaiting_payment", "payment_pending", "verified", "draft"}
            ]
            registration = payable[0] if payable else (registrations[0] if registrations else None)

        if registration is None and payload.order_id is None:
            raise NotFoundException(
                code="REGISTRATION_NOT_FOUND",
                message="Tidak ada registrasi untuk akun ini",
            )

        if payload.order_id is None:
            # Serialize legacy checkout even before an order exists. Locking the
            # user avoids reversing webhook's order -> registration lock order.
            # NO KEY UPDATE still permits FK checks when webhook notifications
            # reference this user, unlike an exclusive FOR UPDATE lock.
            await session.get(User, user_id, with_for_update={"key_share": True})
            latest_order = await PaymentRepository.get_latest_order(session, registration.id)
            if latest_order:
                latest_order = await PaymentRepository.get_order_for_user(session, latest_order.id, user_id, lock=True)
        if latest_order and latest_order.status == OrderStatus.PAID:
            latest_payment = await PaymentRepository.get_payment_by_order(session, latest_order.id)
            payment_id = latest_payment.id if latest_payment else None
            return (
                schemas.DokuCheckoutResponse(
                    payment_url="",
                    already_paid=True,
                    payment_id=payment_id,
                    order_status=latest_order.status,
                    requires_payment=False,
                ),
                latest_order,
            )

        if latest_order is None:
            latest_order = await PaymentRepository.create_order(session, registration.id)
        if latest_order.status not in {OrderStatus.PENDING, OrderStatus.PARTIALLY_PAID, OrderStatus.DRAFT}:
            raise ConflictException("ORDER_NOT_PAYABLE", "Order tidak dapat dibayar")
        if latest_order.currency.upper() != "IDR":
            raise ValidationException("DOKU_IDR_REQUIRED", "DOKU hanya menerima tagihan IDR")
        now = datetime.now(timezone.utc)
        payments = await PaymentRepository.get_payments_by_order(session, latest_order.id)
        active = [item for item in payments if item.transaction_status not in {
            PaymentStatus.SUCCESS, PaymentStatus.FAILED, PaymentStatus.EXPIRED, PaymentStatus.CANCELED,
        }]
        if active:
            # Any provider/method blocks a second bill, including legacy direct VA/QRIS.
            payment = active[0]
            reusable_url = PaymentService._reusable_doku_checkout_url(payment) if (
                len(active) == 1 and PaymentService._is_doku_checkout(payment)
            ) else ""
            if not reusable_url:
                raise ConflictException("PAYMENT_AWAITING_RECONCILIATION", "Pembayaran aktif masih menunggu konfirmasi. Periksa status pembayaran sebelum membuat tagihan baru.")
            paid, remaining = await PaymentService._payment_progress(session, latest_order)
            return (schemas.DokuCheckoutResponse(
                payment_url=reusable_url, expires_at=payment.expired_at, payment_id=payment.id,
                order_status=latest_order.status, payment_sequence=payment.payment_sequence,
                payment_sequence_count=payment.payment_sequence_count,
                payment_amount=float(payment.gross_amount), paid_amount=float(paid), remaining_amount=float(remaining),
            ), latest_order)
        expires = latest_order.expires_at
        if expires and expires.replace(tzinfo=expires.tzinfo or timezone.utc) <= now:
            raise ConflictException("ORDER_EXPIRED", "Order sudah kedaluwarsa")
        sequence, sequence_count, segment_amount = await PaymentService._next_doku_checkout_segment(session, latest_order, payments)
        payment = Payment(
            order_id=latest_order.id, provider="doku", payment_type="doku_checkout",
            gross_amount=segment_amount, currency="IDR", payment_sequence=sequence,
            payment_sequence_count=sequence_count, transaction_status=PaymentStatus.CREATED,
        )
        session.add(payment)
        await session.flush()

        participant = await session.get(ParticipantProfile, registration.participant_id) if registration else None
        user = await session.get(User, participant.user_id) if participant else await session.get(User, user_id)
        event = await session.get(Event, registration.event_id) if registration else None
        if event is None and latest_order.event_id:
            event = await session.get(Event, latest_order.event_id)
        if event is None:
            from app.modules.store.models import Product, OrderItem
            event_id = (await session.execute(select(Product.event_id).join(OrderItem, OrderItem.product_id == Product.id).where(OrderItem.order_id == latest_order.id).limit(1))).scalar_one_or_none()
            event = await session.get(Event, event_id) if event_id else None
        if not user or not event:
            raise ValidationException("PAYMENT_DATA_INCOMPLETE", "Data user atau event tidak lengkap")
        customer_name = participant.full_name if participant else (user.full_name or user.email)
        customer_id = str(participant.id) if participant else str(user.id)
        amount = float(payment.gross_amount)
        if amount.is_integer(): amount = int(amount)
        provider_invoice = f"{latest_order.order_number}-P{payment.payment_sequence:02d}-{uuid.uuid4().hex[:6].upper()}"
        request_body = {
            "order": {
                "amount": amount,
                "invoice_number": provider_invoice,
                "currency": latest_order.currency,
                "callback_url": get_settings().DOKU_CALLBACK_URL,
                "auto_redirect": True,
                "line_items": [{"name": f"{event.name} - pembayaran {payment.payment_sequence}/{payment.payment_sequence_count}", "price": amount, "quantity": 1}],
            },
            "payment": {"payment_due_date": get_settings().DOKU_PAYMENT_DUE_MINUTES},
            "customer": {"id": customer_id, "name": customer_name, "email": user.email, "phone": user.phone or ""},
            "additional_info": {"event_id": str(event.id), "registration_id": str(registration.id) if registration else "", "order_id": str(latest_order.id)},
        }
        client = DokuCheckoutClient()
        client._credentials()  # Fail configuration checks before reserving an attempt.
        request_id = str(uuid.uuid4())
        payment.provider_order_id = provider_invoice
        payment.provider_transaction_id = request_id
        payment.external_id = request_id
        payment.expired_at = now + timedelta(minutes=get_settings().DOKU_PAYMENT_DUE_MINUTES)
        if latest_order.status == OrderStatus.DRAFT:
            latest_order.status = OrderStatus.PENDING
        # Make the invoice visible to webhooks and competing checkout requests BEFORE
        # external I/O. On timeout, retain CREATED until provider reconciliation.
        await session.commit()
        response, _ = await client.create_payment(request_body, request_id=request_id)
        response_payment = response.get("response", {}).get("payment", response.get("payment", {}))
        payment_url = response_payment.get("url")
        if not payment_url:
            raise ValidationException("DOKU_PAYMENT_URL_MISSING", "DOKU tidak mengembalikan payment URL")
        # A verified webhook may have settled this invoice during the HTTP call.
        await session.refresh(payment, with_for_update=True)
        payment.checkout_url = payment_url
        if payment.transaction_status == PaymentStatus.CREATED:
            payment.raw_response = json.dumps(response)
            payment.transaction_status = PaymentStatus.PENDING
        await session.commit()
        await session.refresh(payment)
        await session.refresh(latest_order)
        paid, remaining = await PaymentService._payment_progress(session, latest_order)
        return (
            schemas.DokuCheckoutResponse(
                payment_url=payment_url if payment.transaction_status == PaymentStatus.PENDING else "",
                token=response_payment.get("token_id"),
                expires_at=payment.expired_at,
                already_paid=latest_order.status == OrderStatus.PAID,
                payment_id=payment.id,
                order_status=latest_order.status,
                requires_payment=latest_order.status != OrderStatus.PAID,
                payment_sequence=payment.payment_sequence,
                payment_sequence_count=payment.payment_sequence_count,
                payment_amount=float(payment.gross_amount),
                paid_amount=float(paid),
                remaining_amount=float(remaining),
            ), latest_order,
        )

    @staticmethod
    async def create_midtrans_checkout(
        session: AsyncSession,
        payload: schemas.CreateDokuCheckoutRequest,
        user_id: uuid.UUID,
    ) -> tuple[schemas.MidtransCheckoutResponse, Order]:
        registration = None
        if payload.order_id:
            order = await PaymentRepository.get_order_for_user(session, payload.order_id, user_id)
            if not order:
                raise NotFoundException("ORDER_NOT_FOUND", "Order tidak ditemukan untuk akun ini")
        else:
            registrations = await PaymentRepository.get_registrations_for_user(session, user_id)
            registration = next((row for row in registrations if row.id == payload.registration_id), None)
            if not registration:
                raise NotFoundException("REGISTRATION_NOT_FOUND", "Registrasi tidak ditemukan untuk akun ini")
            order = await PaymentRepository.get_latest_order(session, registration.id)
            if not order or order.status not in {OrderStatus.PENDING, OrderStatus.PARTIALLY_PAID, OrderStatus.DRAFT, OrderStatus.PAID}:
                order = await PaymentRepository.create_order(session, registration.id)

        if order.status == OrderStatus.PAID:
            paid_payment = await PaymentRepository.get_payment_by_order(session, order.id)
            return schemas.MidtransCheckoutResponse(
                payment_url="", token="", already_paid=True,
                payment_id=paid_payment.id if paid_payment else None,
                order_status=order.status, requires_payment=False,
            ), order
        await PaymentService._reject_direct_gateway_for_hari_santri(session, order)
        if order.status not in {OrderStatus.PENDING, OrderStatus.PARTIALLY_PAID, OrderStatus.DRAFT}:
            raise ConflictException("ORDER_NOT_PAYABLE", "Order tidak dapat dibayar")
        if order.currency.upper() != "IDR" or Decimal(str(order.total_amount)) != Decimal(str(order.total_amount)).to_integral_value():
            raise ValidationException("MIDTRANS_IDR_REQUIRED", "Midtrans memerlukan tagihan IDR tanpa desimal")

        sequence, sequence_count, segment_amount = await PaymentService._next_payment_segment(session, order)
        payment = (await session.execute(select(Payment).where(
            Payment.order_id == order.id,
            Payment.provider == "midtrans",
            Payment.payment_sequence == sequence,
            Payment.deleted_at.is_(None),
        ).order_by(Payment.created_at.desc()))).scalars().first()
        now = datetime.now(timezone.utc)
        token = PaymentService._reusable_midtrans_token(payment, now)
        if token:
            return schemas.MidtransCheckoutResponse(
                payment_url=payment.checkout_url, token=token, expires_at=payment.expired_at,
                payment_id=payment.id, order_status=order.status,
                payment_sequence=sequence, payment_sequence_count=sequence_count,
                payment_amount=float(segment_amount),
                paid_amount=float((await PaymentService._payment_progress(session, order))[0]),
                remaining_amount=float((await PaymentService._payment_progress(session, order))[1]),
            ), order

        # Preserve the old attempt so a delayed Midtrans webhook can still be
        # matched by its provider_order_id. A retry gets its own payment row.
        if payment:
            expires_at = payment.expired_at
            if expires_at and expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if (
                payment.transaction_status in {PaymentStatus.CREATED, PaymentStatus.PENDING}
                and (expires_at is None or expires_at <= now)
            ):
                payment.transaction_status = PaymentStatus.EXPIRED
                payment.expired_at = expires_at or now
            payment = None

        if registration is None and order.registration_id:
            registration = await session.get(Registration, order.registration_id)
        participant = await session.get(ParticipantProfile, registration.participant_id) if registration else None
        user = await session.get(User, participant.user_id) if participant else await session.get(User, user_id)
        if not user:
            raise ValidationException("PAYMENT_DATA_INCOMPLETE", "Data pengguna tidak lengkap")

        settings = get_settings()
        midtrans_order_id = f"{order.order_number}-MT-{uuid.uuid4().hex[:8].upper()}"
        request_body = {
            "transaction_details": {
                "order_id": midtrans_order_id,
                "gross_amount": int(segment_amount),
            },
            "customer_details": {
                "first_name": (participant.full_name if participant else user.full_name) or user.email,
                "email": user.email,
                "phone": user.phone or "",
            },
            "expiry": {"unit": "minutes", "duration": settings.MIDTRANS_PAYMENT_DUE_MINUTES},
            "callbacks": {"finish": settings.MIDTRANS_CALLBACK_URL},
            "custom_field1": str(order.id),
            "custom_field2": str(registration.id) if registration else "",
        }
        response = await MidtransClient().create_snap_transaction(request_body)
        token, payment_url = str(response.get("token") or ""), str(response.get("redirect_url") or "")
        if not token or not payment_url:
            raise ValidationException("MIDTRANS_PAYMENT_URL_MISSING", "Midtrans tidak mengembalikan token dan payment URL")
        if not payment:
            payment = Payment(order_id=order.id, provider="midtrans", gross_amount=segment_amount, currency=order.currency, payment_sequence=sequence, payment_sequence_count=sequence_count)
            session.add(payment)
        payment.provider_order_id = midtrans_order_id
        payment.payment_type = "midtrans_snap"
        payment.checkout_url = payment_url
        payment.raw_response = json.dumps(response)
        payment.transaction_status = PaymentStatus.PENDING
        payment.expired_at = datetime.now(timezone.utc) + timedelta(minutes=settings.MIDTRANS_PAYMENT_DUE_MINUTES)
        await session.commit()
        await session.refresh(payment)
        paid, remaining = await PaymentService._payment_progress(session, order)
        return schemas.MidtransCheckoutResponse(
            payment_url=payment_url, token=token, expires_at=payment.expired_at,
            payment_id=payment.id, order_status=order.status,
            payment_sequence=sequence, payment_sequence_count=sequence_count,
            payment_amount=float(segment_amount), paid_amount=float(paid), remaining_amount=float(remaining),
        ), order

    @staticmethod
    async def handle_midtrans_notification(session: AsyncSession, payload: dict[str, Any]) -> str:
        settings = get_settings()
        if not verify_notification_signature(payload, settings.MIDTRANS_SERVER_KEY):
            raise ValidationException("MIDTRANS_INVALID_SIGNATURE", "Signature notifikasi Midtrans tidak valid")
        provider_order_id = str(payload.get("order_id") or "")
        if not provider_order_id:
            raise ValidationException("MIDTRANS_INVALID_PAYLOAD", "order_id wajib ada")

        # Server-to-server verification prevents a valid-looking callback from
        # becoming the sole source of truth for money movement.
        verified = await MidtransClient().transaction_status(provider_order_id)
        for key in ("order_id", "transaction_status", "gross_amount"):
            if str(verified.get(key)) != str(payload.get(key)):
                raise ValidationException("MIDTRANS_STATUS_MISMATCH", "Status notifikasi tidak sesuai API Midtrans")

        payment = await PaymentRepository.get_payment_by_provider_order_id(session, provider_order_id, lock=True, provider="midtrans")
        if not payment:
            raise NotFoundException("MIDTRANS_PAYMENT_NOT_FOUND", "Payment Midtrans tidak ditemukan")
        order = await session.get(Order, payment.order_id, with_for_update=True)
        if not order:
            raise NotFoundException("MIDTRANS_ORDER_NOT_FOUND", "Order Midtrans tidak ditemukan")
        if Decimal(str(verified.get("gross_amount"))) != Decimal(str(payment.gross_amount)):
            raise ValidationException("MIDTRANS_AMOUNT_MISMATCH", "Nominal Midtrans tidak sesuai bagian pembayaran")

        transaction_status = str(verified.get("transaction_status") or "").lower()
        fraud_status = str(verified.get("fraud_status") or "").lower()
        event_id = f"{verified.get('transaction_id') or provider_order_id}:{transaction_status}:{verified.get('status_code')}"
        if await PaymentRepository.get_webhook_event(session, event_id, "midtrans"):
            return "already_processed"

        successful = transaction_status == "settlement" or (transaction_status == "capture" and fraud_status == "accept")
        if successful:
            payment.transaction_status = PaymentStatus.SUCCESS
            payment.paid_at = datetime.now(timezone.utc)
        elif transaction_status in {"deny", "cancel", "failure"}:
            payment.transaction_status = PaymentStatus.FAILED
        elif transaction_status == "expire":
            payment.transaction_status = PaymentStatus.EXPIRED
            payment.expired_at = datetime.now(timezone.utc)
        elif transaction_status in {"refund", "partial_refund"}:
            payment.transaction_status = PaymentStatus.REFUNDED
        else:
            payment.transaction_status = PaymentStatus.PENDING

        payment.provider_transaction_id = str(verified.get("transaction_id") or "") or None
        payment.payment_type = str(verified.get("payment_type") or payment.payment_type)
        payment.channel_code = normalize_midtrans_channel(verified)
        payment.gross_amount = Decimal(str(verified.get("gross_amount")))
        payment.currency = str(verified.get("currency") or order.currency).upper()
        payment.fraud_status = fraud_status or None
        safe_payload = {key: value for key, value in verified.items() if key != "signature_key"}
        payment.raw_response = json.dumps(safe_payload)
        _, remaining, became_paid = await PaymentService._reconcile_order_payment(session, order)
        await PaymentService._notify_payment_status(session, order, payment)
        session.add(PaymentWebhookEvent(
            payment_id=payment.id, provider="midtrans", request_id=event_id,
            event_status=transaction_status.upper(), payload=safe_payload,
        ))
        await session.commit()
        if successful and became_paid and remaining == 0:
            asyncio.create_task(deliver_payment_for_order(order.id))
        return payment.transaction_status

    @staticmethod
    async def get_payment(session: AsyncSession, payment_id: uuid.UUID, user_id: uuid.UUID):
        payment = await PaymentRepository.get_payment_for_user(session, payment_id, user_id)
        if not payment:
            from app.core.exceptions import NotFoundException

            raise NotFoundException(code="PAYMENT_NOT_FOUND", message="Payment tidak ditemukan")
        return payment

    @staticmethod
    async def get_order(session: AsyncSession, order_id: uuid.UUID, user_id: uuid.UUID):
        order = await PaymentRepository.get_order_for_user(session, order_id, user_id)
        if not order:
            raise NotFoundException("ORDER_NOT_FOUND", "Order tidak ditemukan")
        return order

    @staticmethod
    async def _user_order_detail(session: AsyncSession, order: Order) -> schemas.UserOrderDetail:
        from app.modules.store.models import OrderItem

        items = list((await session.execute(
            select(OrderItem).where(OrderItem.order_id == order.id).order_by(OrderItem.id)
        )).scalars().all())

        # Pending orders may contain a product-name snapshot from before an
        # organizer renamed the package. Resolve the current catalog name for
        # unpaid orders while preserving paid invoices as historical records.
        current_names: dict[uuid.UUID, str] = {}
        rate_names: dict[uuid.UUID, str] = {}
        if "continue_payment" in order.allowed_actions:
            product_ids = [item.product_id for item in items if item.product_id]
            if product_ids:
                products = (await session.execute(
                    select(Product.id, Product.name, DelegatePackage.name, DelegatePackageRate.name)
                    .outerjoin(DelegatePackageRate, DelegatePackageRate.id == Product.delegate_package_rate_id)
                    .outerjoin(DelegatePackage, DelegatePackage.id == DelegatePackageRate.delegate_package_id)
                    .where(Product.id.in_(product_ids))
                )).all()
                current_names.update({
                    product_id: f"{package_name} - {rate_name}" if package_name and rate_name else name
                    for product_id, name, package_name, rate_name in products
                    if (package_name and rate_name) or name
                })

            rate_ids = []
            for item in items:
                rate_id = (item.metadata_json or {}).get("delegate_package_rate_id")
                if rate_id:
                    try:
                        rate_ids.append(uuid.UUID(str(rate_id)))
                    except (TypeError, ValueError):
                        continue
            if rate_ids:
                rates = (await session.execute(
                    select(DelegatePackageRate.id, DelegatePackage.name, DelegatePackageRate.name)
                    .join(DelegatePackage, DelegatePackage.id == DelegatePackageRate.delegate_package_id)
                    .where(DelegatePackageRate.id.in_(rate_ids))
                )).all()
                rate_names.update({rate_id: f"{package_name} - {rate_name}" for rate_id, package_name, rate_name in rates})

        def display_name(item: OrderItem) -> str:
            rate_id = (item.metadata_json or {}).get("delegate_package_rate_id")
            try:
                rate_name = rate_names.get(uuid.UUID(str(rate_id)))
            except (TypeError, ValueError):
                rate_name = None
            return rate_name or current_names.get(item.product_id) or item.product_name

        payments = await PaymentRepository.get_payments_by_order(session, order.id)
        paid_amount, remaining_amount = await PaymentService._payment_progress(session, order)
        # Keep hidden attempts in gateway lookup and accounting. A late settlement
        # or refund must always become visible again in the user's history.
        payments = [payment for payment in payments if not payment.hidden_from_user_at
                    or payment.transaction_status in {PaymentStatus.SUCCESS, PaymentStatus.REFUNDED}]
        return schemas.UserOrderDetail(
            order=schemas.OrderRead.model_validate(order),
            items=[schemas.OrderItemRead(
                id=item.id, product_id=item.product_id,
                product_code=item.product_code, product_name=display_name(item),
                product_type=item.product_type, quantity=item.quantity,
                unit_price=float(item.unit_price), currency=item.currency,
                line_total=float(item.line_total), metadata=dict(item.metadata_json or {}),
            ) for item in items],
            latest_payment=schemas.PaymentRead.model_validate(payments[0]) if payments else None,
            payment_attempts=[schemas.PaymentRead.model_validate(payment) for payment in payments],
            paid_amount=float(paid_amount),
            remaining_amount=float(remaining_amount),
            is_payment_complete=remaining_amount == 0,
        )

    @staticmethod
    async def list_user_orders(
        session: AsyncSession, user_id: uuid.UUID, *, status: str | None,
        event_id: uuid.UUID | None, page: int, size: int,
    ) -> tuple[list[schemas.UserOrderDetail], int]:
        valid_statuses = {
            OrderStatus.DRAFT, OrderStatus.PENDING, OrderStatus.PARTIALLY_PAID, OrderStatus.PAID,
            OrderStatus.EXPIRED, OrderStatus.CANCELED,
        }
        if status and status not in valid_statuses:
            raise ValidationException("INVALID_ORDER_STATUS", "Status order tidak valid")
        filters = [Order.user_id == user_id]
        if status:
            filters.append(Order.status == status)
        if event_id:
            filters.append(Order.event_id == event_id)
        total = int((await session.execute(select(func.count(Order.id)).where(*filters))).scalar_one())
        orders = await PaymentRepository.list_orders_for_user(
            session, user_id, status=status, event_id=event_id,
            offset=(page - 1) * size, limit=size,
        )
        return [await PaymentService._user_order_detail(session, order) for order in orders], total

    @staticmethod
    async def get_user_order_detail(
        session: AsyncSession, order_id: uuid.UUID, user_id: uuid.UUID,
    ) -> schemas.UserOrderDetail:
        order = await PaymentRepository.get_order_for_user(session, order_id, user_id)
        if not order:
            raise NotFoundException("ORDER_NOT_FOUND", "Order tidak ditemukan untuk akun ini")
        return await PaymentService._user_order_detail(session, order)

    @staticmethod
    async def delete_user_payment_attempts(session, order_id, user_id, payment_ids):
        order = await PaymentRepository.get_order_for_user(session, order_id, user_id, lock=True)
        if not order or order.user_id != user_id:
            raise NotFoundException("ORDER_NOT_FOUND", "Order tidak ditemukan untuk akun ini")
        payments = await PaymentRepository.get_payments_by_order(session, order.id, lock=True)
        paid, remaining = await PaymentService._payment_progress(session, order)
        if order.status != OrderStatus.PAID or remaining != 0 or paid <= 0:
            raise ConflictException("ORDER_NOT_FULLY_PAID", "Percobaan pembayaran hanya dapat dihapus setelah order lunas")
        selected_ids = set(payment_ids)
        selected = [payment for payment in payments if payment.id in selected_ids]
        if len(selected) != len(selected_ids):
            raise NotFoundException("PAYMENT_NOT_FOUND", "Percobaan pembayaran tidak ditemukan pada order ini")
        removable = {PaymentStatus.CREATED, PaymentStatus.PENDING, PaymentStatus.FAILED,
                     PaymentStatus.EXPIRED, PaymentStatus.CANCELED}
        if any(payment.transaction_status not in removable for payment in selected):
            raise ConflictException("PAYMENT_DELETE_FORBIDDEN", "Pembayaran sukses atau refund tidak dapat dihapus")
        now = datetime.now(timezone.utc)
        for payment in selected:
            if payment.hidden_from_user_at is not None:
                continue
            payment.hidden_from_user_at = now
            session.add(PaymentWebhookEvent(
                payment_id=payment.id, provider=payment.provider,
                request_id=f"user-hide-{uuid.uuid4().hex}", event_status="USER_HIDDEN",
                payload={"hidden_by": str(user_id), "hidden_at": now.isoformat()},
            ))
        await session.commit()
        return sorted(selected_ids, key=str)

    @staticmethod
    async def cancel_user_order(
        session: AsyncSession, order_id: uuid.UUID, user_id: uuid.UUID,
        reason: str | None = None,
    ) -> schemas.UserOrderDetail:
        order = await PaymentRepository.get_order_for_user(session, order_id, user_id, lock=True)
        if not order:
            raise NotFoundException("ORDER_NOT_FOUND", "Order tidak ditemukan untuk akun ini")
        payments = await PaymentRepository.get_payments_by_order(session, order.id, lock=True)
        if order.status == OrderStatus.PAID or any(
            payment.transaction_status == PaymentStatus.SUCCESS for payment in payments
        ):
            raise ConflictException("PAID_ORDER_CANCEL_FORBIDDEN", "Order yang sudah dibayar tidak dapat dibatalkan")
        if order.status == OrderStatus.CANCELED and order.canceled_by is not None:
            return await PaymentService._user_order_detail(session, order)

        now = datetime.now(timezone.utc)
        order.status, order.canceled_at, order.canceled_by = OrderStatus.CANCELED, now, user_id
        order.cancellation_reason = reason.strip() if reason else None
        for payment in payments:
            if payment.transaction_status in {PaymentStatus.CREATED, PaymentStatus.PENDING}:
                payment.transaction_status = PaymentStatus.CANCELED
                session.add(PaymentWebhookEvent(
                    payment_id=payment.id, provider=payment.provider,
                    request_id=f"user-cancel-{uuid.uuid4().hex}", event_status="CANCELED",
                    payload={"canceled_by": str(user_id), "reason": order.cancellation_reason, "canceled_at": now.isoformat()},
                ))
        await session.commit()
        await session.refresh(order)
        return await PaymentService._user_order_detail(session, order)

    @staticmethod
    async def continue_user_order_payment(
        session: AsyncSession, order_id: uuid.UUID, user_id: uuid.UUID, provider: str,
    ) -> tuple[schemas.DokuCheckoutResponse, Order]:
        order = await PaymentRepository.get_order_for_user(session, order_id, user_id, lock=True)
        if not order:
            raise NotFoundException("ORDER_NOT_FOUND", "Order tidak ditemukan untuk akun ini")
        await PaymentService._reject_direct_gateway_for_hari_santri(session, order)
        if "continue_payment" not in order.allowed_actions:
            raise ConflictException("ORDER_NOT_PAYABLE", "Order tidak dapat dilanjutkan ke pembayaran")
        payments = await PaymentRepository.get_payments_by_order(session, order.id, lock=True)
        _, remaining, _ = await PaymentService._reconcile_order_payment(session, order)
        if remaining == 0:
            await session.commit()
            raise ConflictException("ORDER_ALREADY_PAID", "Order sudah dibayar")

        order.status = OrderStatus.PARTIALLY_PAID if any(payment.transaction_status == PaymentStatus.SUCCESS for payment in payments) else OrderStatus.PENDING
        order.canceled_at = order.canceled_by = order.cancellation_reason = None
        await session.flush()
        checkout_payload = schemas.CreateDokuCheckoutRequest(order_id=order.id)
        if provider == "doku":
            return await PaymentService.create_doku_checkout(session, checkout_payload, user_id)
        if provider == "midtrans":
            return await PaymentService.create_midtrans_checkout(session, checkout_payload, user_id)
        raise ValidationException("INVALID_PAYMENT_PROVIDER", "Provider pembayaran tidak didukung")

    @staticmethod
    async def get_invoice(session: AsyncSession, registration_ref: str | uuid.UUID, user_id: uuid.UUID) -> schemas.InvoiceRead:
        reg = await PaymentRepository.get_registration(session, registration_ref)
        participant = await session.get(ParticipantProfile, reg.participant_id)
        if not participant:
            from app.core.exceptions import NotFoundException

            raise NotFoundException(code="PARTICIPANT_NOT_FOUND", message="Profil peserta tidak ditemukan")
        if participant.user_id != user_id:
            raise NotFoundException(code="REGISTRATION_NOT_FOUND", message="Registrasi tidak ditemukan")

        user = await session.get(User, participant.user_id)
        event = await session.get(Event, reg.event_id)
        detail = await session.get(DelegateRegistrationDetail, reg.id)
        package = await session.get(DelegatePackage, detail.delegate_package_id) if detail else None
        order = await PaymentRepository.get_latest_order(session, reg.id)
        payment = None
        if order:
            payment = await PaymentRepository.get_payment_by_order(session, order.id)

        order_data = None
        if order:
            order_data = schemas.OrderRead.model_validate(order)
        payment_data = None
        if payment:
            payment_data = schemas.PaymentRead.model_validate(payment)

        return schemas.InvoiceRead(
            registration=schemas.InvoiceRegistration(
                id=reg.id,
                registration_number=reg.registration_number,
                status=reg.status,
                event_id=reg.event_id,
                event_name=event.name if event else None,
                participant_id=reg.participant_id,
                delegate_package_id=package.id if package else None,
                delegate_package_code=package.code if package else None,
                delegate_package_name=package.name if package else None,
                confirmed_at=reg.confirmed_at,
            ),
            participant=schemas.InvoiceParticipant(
                id=participant.id,
                full_name=participant.full_name,
                organization_name=participant.organization_name,
                email=user.email if user else "",
            ),
            order=order_data,
            payment=payment_data,
        )

    @staticmethod
    async def get_my_invoices(session: AsyncSession, user_id: uuid.UUID, event_id: uuid.UUID | None = None) -> list[schemas.InvoiceRead]:
        registrations = await PaymentRepository.get_registrations_for_user(session, user_id, event_id)
        if not registrations:
            return []

        result: list[schemas.InvoiceRead] = []
        for reg in registrations:
            participant = await session.get(ParticipantProfile, reg.participant_id)
            if not participant:
                continue
            user = await session.get(User, participant.user_id)
            event = await session.get(Event, reg.event_id)
            detail = await session.get(DelegateRegistrationDetail, reg.id)
            package = await session.get(DelegatePackage, detail.delegate_package_id) if detail else None
            order = await PaymentRepository.get_latest_order(session, reg.id)
            payment = await PaymentRepository.get_payment_by_order(session, order.id) if order else None

            registration = schemas.InvoiceRegistration(
                id=reg.id,
                registration_number=reg.registration_number,
                status=reg.status,
                event_id=reg.event_id,
                event_name=event.name if event else None,
                participant_id=reg.participant_id,
                delegate_package_id=package.id if package else None,
                delegate_package_code=package.code if package else None,
                delegate_package_name=package.name if package else None,
                confirmed_at=reg.confirmed_at,
            )
            participant_info = schemas.InvoiceParticipant(
                id=participant.id,
                full_name=participant.full_name,
                organization_name=participant.organization_name,
                email=user.email if user else "",
            )
            result.append(
                schemas.InvoiceRead(
                    registration=registration,
                    participant=participant_info,
                    order=schemas.OrderRead.model_validate(order) if order else None,
                    payment=schemas.PaymentRead.model_validate(payment) if payment else None,
                )
            )
        return result

    @staticmethod
    async def handle_doku_notification(session: AsyncSession, body: bytes, headers: dict[str, str]) -> str:
        settings = get_settings()
        client_id = headers.get("client-id", "")
        request_id = headers.get("request-id", "")
        timestamp = headers.get("request-timestamp", "")
        signature = headers.get("signature", "")
        if not settings.DOKU_CLIENT_ID or not settings.DOKU_SECRET_KEY:
            raise ValidationException("DOKU_NOT_CONFIGURED", "DOKU belum dikonfigurasi")
        if client_id != settings.DOKU_CLIENT_ID or not all((request_id, timestamp, signature)):
            raise ValidationException("DOKU_INVALID_HEADERS", "Header notifikasi DOKU tidak valid")
        if not verify_signature(signature, client_id, request_id, timestamp, settings.DOKU_NOTIFICATION_PATH, body, settings.DOKU_SECRET_KEY):
            raise ValidationException("DOKU_INVALID_SIGNATURE", "Signature notifikasi DOKU tidak valid")
        if await PaymentRepository.get_webhook_event(session, request_id):
            return "already_processed"
        try:
            payload: dict[str, Any] = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValidationException("DOKU_INVALID_PAYLOAD", "Payload notifikasi DOKU tidak valid") from exc
        order_data = payload.get("order", {})
        transaction = payload.get("transaction", {})
        order_number = order_data.get("invoice_number")
        status = str(transaction.get("status") or payload.get("payment", {}).get("status") or "").upper()
        if not order_number or not status:
            raise ValidationException("DOKU_INVALID_PAYLOAD", "Invoice number dan transaction status wajib ada")
        payment = await PaymentRepository.get_payment_by_provider_order_id(session, order_number, provider="doku")
        if not payment:
            raise NotFoundException("DOKU_PAYMENT_NOT_FOUND", "Payment DOKU tidak ditemukan")
        order = await session.get(Order, payment.order_id, with_for_update=True)
        if not order:
            raise NotFoundException("DOKU_ORDER_NOT_FOUND", "Order DOKU tidak ditemukan")
        # Match checkout/resume/cancel lock order, then reload after waiting.
        await session.refresh(payment, with_for_update=True)
        if await PaymentRepository.get_webhook_event(session, request_id):
            return "already_processed"
        notified_amount = Decimal(str(order_data.get("amount")))
        if notified_amount != Decimal(str(payment.gross_amount)):
            raise ValidationException("DOKU_AMOUNT_MISMATCH", "Nominal notifikasi DOKU tidak sesuai bagian pembayaran")
        if payment.transaction_status == PaymentStatus.SUCCESS:
            pass
        elif status == "SUCCESS":
            payment.transaction_status = PaymentStatus.SUCCESS
            payment.paid_at = datetime.now(timezone.utc)
        elif PaymentService._is_doku_checkout(payment) and status in {"FAILED", "TIMEOUT", "REDIRECT", "PENDING"}:
            payment.transaction_status = PaymentStatus.PENDING
        elif status in {"FAILED", "CANCELLED", "CANCELED"}:
            payment.transaction_status = PaymentStatus.FAILED
        elif status == "EXPIRED":
            payment.transaction_status = PaymentStatus.EXPIRED
            payment.expired_at = datetime.now(timezone.utc)
        else:
            payment.transaction_status = status.lower()
        payment.raw_response = json.dumps(payload)
        _, remaining, became_paid = await PaymentService._reconcile_order_payment(session, order)
        await PaymentService._notify_payment_status(session, order, payment)
        session.add(PaymentWebhookEvent(payment_id=payment.id, provider="doku", request_id=request_id, event_status=status, payload=payload))
        await session.commit()
        if status == "SUCCESS" and became_paid and remaining == 0:
            asyncio.create_task(deliver_payment_for_order(order.id))
        return status.lower()
