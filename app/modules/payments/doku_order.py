"""Order-first DOKU pilot. Existing checkout and registration APIs stay intact."""
import json
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.dependencies import get_current_user, get_db_session
from app.core.exceptions import ConflictException, NotFoundException, ValidationException
from app.modules.payments.doku import DokuCheckoutClient
from app.modules.payments.doku_snap import DokuSnapClient
from app.modules.payments.models import Order, OrderStatus, Payment, PaymentChannel, PaymentStatus
from app.modules.payments.service import PaymentService
from app.modules.users.models import User
from app.support.responses import success_response

router = APIRouter()
BANKS = ("BCA", "BNI", "MANDIRI", "BSI", "BRI")
ACTIVE = (PaymentStatus.CREATED, PaymentStatus.PENDING)


class DokuOrderChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    method: Literal["virtual_account", "qris", "credit_card"]
    bank_code: Literal["BCA", "BNI", "MANDIRI", "BSI", "BRI"] | None = None

    @model_validator(mode="after")
    def bank_matches_method(self):
        if (self.method == "virtual_account") != (self.bank_code is not None):
            raise ValueError("Choose a bank only for Virtual Account")
        return self


def utc(value):
    return value.replace(tzinfo=timezone.utc) if value and value.tzinfo is None else value


def result(order, payment):
    method = {"doku_snap_va": "virtual_account", "doku_snap_qris": "qris", "CREDIT_CARD": "credit_card"}.get(payment.payment_type)
    if not method:
        raise ConflictException("PAYMENT_CHANNEL_ALREADY_SELECTED", "Order memiliki pembayaran aktif melalui metode lain. Periksa status pembayaran tersebut.")
    ready = payment.virtual_account_no if method == "virtual_account" else payment.payment_instructions_url if method == "qris" else payment.checkout_url
    if not ready:
        raise ConflictException("DOKU_PAYMENT_IN_PROGRESS", "Permintaan DOKU sedang diproses atau membutuhkan pengecekan organizer. Jangan membuat tagihan baru.")
    return {
        "order_id": str(order.id), "order_number": order.order_number,
        "payment_id": str(payment.id), "method": method,
        "bank_code": payment.channel_code, "status": payment.transaction_status,
        "amount": float(payment.gross_amount), "currency": payment.currency,
        "expires_at": payment.expired_at, "payment_sequence": payment.payment_sequence,
        "payment_sequence_count": payment.payment_sequence_count,
        "virtual_account_no": payment.virtual_account_no if method == "virtual_account" else None,
        "qr_content": payment.payment_instructions_url if method == "qris" else None,
        "payment_url": payment.checkout_url if method == "credit_card" else None,
    }


async def capabilities(db):
    settings = get_settings()
    channels = DokuSnapClient().va_channels()
    card = (await db.execute(select(PaymentChannel.id).where(
        PaymentChannel.provider == "doku", PaymentChannel.is_enabled.is_(True),
        PaymentChannel.code == "CREDIT_CARD",
    ).limit(1))).scalar_one_or_none()
    return {
        "virtual_accounts": [bank for bank in BANKS if channels.get(bank, {}).get("partner_service_id")],
        "qris": bool(settings.DOKU_QRIS_MERCHANT_ID and settings.DOKU_QRIS_TERMINAL_ID),
        "credit_card": card is not None,
    }


async def owned_order(db, order_id, user_id):
    order = (await db.execute(select(Order).where(Order.id == order_id, Order.user_id == user_id).with_for_update())).scalar_one_or_none()
    if not order:
        raise NotFoundException("ORDER_NOT_FOUND", "Order tidak ditemukan untuk akun ini")
    await PaymentService._reject_direct_gateway_for_hari_santri(db, order)
    return order


async def active_payment(db, order):
    # The order row lock serializes requests from this pilot across browser tabs.
    return (await db.execute(select(Payment).where(
        Payment.order_id == order.id, Payment.deleted_at.is_(None),
        Payment.transaction_status.in_(ACTIVE),
    ).order_by(Payment.created_at.desc()).limit(1))).scalar_one_or_none()


async def create_payment(db, order_id, choice, user):
    order = await owned_order(db, order_id, user.id)
    if order.status not in (OrderStatus.DRAFT, OrderStatus.PENDING, OrderStatus.PARTIALLY_PAID):
        raise ConflictException("ORDER_NOT_PAYABLE", "Order tidak dapat dibayar")
    if order.currency.upper() != "IDR":
        raise ValidationException("DOKU_IDR_REQUIRED", "DOKU hanya menerima tagihan IDR")
    now = datetime.now(timezone.utc)
    if order.expires_at and utc(order.expires_at) <= now:
        raise ConflictException("ORDER_EXPIRED", "Order sudah kedaluwarsa. Kembali ke keranjang untuk melanjutkan.")
    existing = await active_payment(db, order)
    if existing:
        # Never create another invoice on a network retry or switch channels
        # while the backend still considers a payment active.
        data = result(order, existing)
        if data["method"] != choice.method or (choice.bank_code and existing.channel_code != choice.bank_code):
            raise ConflictException("PAYMENT_CHANNEL_ALREADY_SELECTED", "Gunakan pembayaran aktif sebelum memilih metode lain.")
        if existing.expired_at and utc(existing.expired_at) <= now:
            raise ConflictException("PAYMENT_AWAITING_RECONCILIATION", "Pembayaran kedaluwarsa masih menunggu pembaruan status. Periksa status pembayaran.")
        return data
    available = await capabilities(db)
    enabled = choice.bank_code in available["virtual_accounts"] if choice.method == "virtual_account" else available[choice.method]
    if not enabled:
        raise ValidationException("DOKU_CHANNEL_NOT_ENABLED", "Metode DOKU ini belum aktif")
    sequence, sequence_count, segment_amount = await PaymentService._next_payment_segment(db, order)
    _, remaining = await PaymentService._payment_progress(db, order)
    # Only QRIS is capped; VA and cards collect the full remaining balance.
    if choice.method != "qris":
        sequence_count = sequence
    amount = Decimal(str(segment_amount if choice.method == "qris" else remaining)).quantize(Decimal("0.01"))
    if amount <= 0:
        raise ConflictException("ORDER_ALREADY_PAID", "Order sudah lunas")
    expires = min(utc(order.expires_at), now + timedelta(minutes=get_settings().DOKU_PAYMENT_DUE_MINUTES)) if order.expires_at else now + timedelta(minutes=get_settings().DOKU_PAYMENT_DUE_MINUTES)
    reference = f"DP{uuid.uuid4().hex[:24].upper()}"
    payment = Payment(
        order_id=order.id, provider="doku_snap_qris" if choice.method == "qris" else "doku",
        payment_type={"virtual_account": "doku_snap_va", "qris": "doku_snap_qris", "credit_card": "CREDIT_CARD"}[choice.method],
        channel_code=choice.bank_code, gross_amount=amount, currency="IDR",
        payment_sequence=sequence, payment_sequence_count=sequence_count,
        provider_order_id=reference, transaction_status=PaymentStatus.CREATED, expired_at=expires,
    )
    db.add(payment)
    await db.flush()
    # Persist the attempt before external I/O. A timeout is ambiguous: retain
    # CREATED and require reconciliation instead of issuing a duplicate invoice.
    await db.commit()
    if choice.method == "virtual_account":
        body = {
            "customerNo": "0", "virtualAccountNo": "", "virtualAccountName": (user.full_name or user.email)[:255],
            "virtualAccountEmail": user.email, "virtualAccountPhone": user.phone or "", "trxId": reference,
            "totalAmount": {"value": f"{amount:.2f}", "currency": "IDR"}, "virtualAccountTrxType": "C",
            "expiredDate": expires.astimezone().isoformat(timespec="seconds"),
            "additionalInfo": {"channel": f"VIRTUAL_ACCOUNT_{choice.bank_code}"},
        }
        response, external_id = await DokuSnapClient().create_va(choice.bank_code, body)
        va = response.get("virtualAccountData") or {}
        payment.virtual_account_no = str(va.get("virtualAccountNo") or "").strip()
        if not payment.virtual_account_no:
            raise ValidationException("DOKU_VA_NUMBER_MISSING", "DOKU tidak mengembalikan nomor Virtual Account")
        payment.provider_reference_no = str(va.get("trxId") or response.get("referenceNo") or "") or None
        payment.payment_instructions_url = (va.get("additionalInfo") or {}).get("howToPayPage")
    elif choice.method == "qris":
        response, external_id = await DokuSnapClient().create_qris({
            "partnerReferenceNo": reference, "amount": {"value": f"{amount:.2f}", "currency": "IDR"},
            "validityPeriod": expires.astimezone().isoformat(timespec="seconds"),
            "additionalInfo": {"feeType": "1", "orderId": str(order.id), "paymentSequence": sequence},
        })
        payment.payment_instructions_url = str(response.get("qrContent") or "")
        if not payment.payment_instructions_url:
            raise ValidationException("DOKU_QRIS_CONTENT_MISSING", "DOKU tidak mengembalikan konten QRIS")
        payment.provider_reference_no = response.get("referenceNo")
    else:
        checkout_amount = int(amount) if amount == amount.to_integral_value() else float(amount)
        response, external_id = await DokuCheckoutClient().create_payment({
            "order": {"amount": checkout_amount, "invoice_number": reference, "currency": "IDR",
                      "callback_url": get_settings().DOKU_CALLBACK_URL, "auto_redirect": True,
                      "line_items": [{"name": f"IWBIF {order.order_number} ({sequence}/{sequence_count})", "price": checkout_amount, "quantity": 1}]},
            "payment": {"payment_due_date": max(1, int((expires - now).total_seconds() / 60)), "payment_method_types": ["CREDIT_CARD"]},
            "customer": {"id": str(user.id), "name": user.full_name or user.email, "email": user.email, "phone": user.phone or ""},
            "additional_info": {"order_id": str(order.id)},
        })
        payment.checkout_url = (response.get("response", {}).get("payment", response.get("payment", {}))).get("url")
        if not payment.checkout_url:
            raise ValidationException("DOKU_PAYMENT_URL_MISSING", "DOKU tidak mengembalikan payment URL")
    payment.external_id = external_id
    payment.provider_transaction_id = external_id
    payment.raw_response = json.dumps(response)
    payment.transaction_status = PaymentStatus.PENDING
    if order.status == OrderStatus.DRAFT:
        order.status = OrderStatus.PENDING
    await db.commit()
    await db.refresh(payment)
    return result(order, payment)


@router.get("/payments/doku/order-methods")
async def methods(request: Request, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db_session)):
    return success_response("Metode DOKU", data=await capabilities(db), request=request)


@router.get("/payments/doku/orders/{order_id}/active")
async def resume(order_id: uuid.UUID, request: Request, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db_session)):
    order = await owned_order(db, order_id, user.id)
    payment = await active_payment(db, order)
    return success_response("Pembayaran aktif", data=result(order, payment) if payment else None, request=request)


@router.post("/payments/doku/orders/{order_id}/checkout")
async def checkout(order_id: uuid.UUID, payload: DokuOrderChoice, request: Request, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db_session)):
    return success_response("Pembayaran DOKU", data=await create_payment(db, order_id, payload, user), request=request)
