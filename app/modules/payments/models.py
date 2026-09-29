import uuid

from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, JSON, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class OrderStatus(str):
    DRAFT = "draft"
    PENDING = "pending"
    PARTIALLY_PAID = "partially_paid"
    PAID = "paid"
    EXPIRED = "expired"
    CANCELED = "canceled"


class OrderKind(str):
    LEGACY = "legacy"
    MAIN_REGISTRATION = "main_registration"
    ADDITIONAL = "additional"
    EXHIBITOR = "exhibitor"
    HARI_SANTRI = "hari_santri_registration"


class PaymentStatus(str):
    CREATED = "created"
    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"
    EXPIRED = "expired"
    REFUNDED = "refunded"
    CANCELED = "canceled"


def payment_allowed_actions(status: str, deleted_at: datetime | None = None) -> list[str]:
    """Return commands accepted by organizer transaction endpoints."""
    if deleted_at is not None or status == PaymentStatus.REFUNDED:
        return []
    if status == PaymentStatus.SUCCESS:
        return ["paid", "success"]
    if status in {
        PaymentStatus.CREATED, PaymentStatus.PENDING, PaymentStatus.FAILED,
        PaymentStatus.EXPIRED, PaymentStatus.CANCELED,
    }:
        return ["paid", "success", "canceled", "delete"]
    return []


def order_allowed_actions(
    status: str,
    canceled_by: uuid.UUID | None = None,
) -> list[str]:
    if status == OrderStatus.PAID or (status == OrderStatus.CANCELED and canceled_by is not None):
        return []
    if status in {OrderStatus.DRAFT, OrderStatus.PENDING, OrderStatus.PARTIALLY_PAID, OrderStatus.EXPIRED, OrderStatus.CANCELED}:
        return ["continue_payment", "cancel"]
    return []


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    registration_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("registrations.id"), nullable=True)
    event_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("events.id", ondelete="SET NULL"), nullable=True)
    order_number: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    order_kind: Mapped[str] = mapped_column(String(30), nullable=False, default=OrderKind.LEGACY)
    subtotal: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    discount_amount: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    tax_amount: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    service_fee: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    total_amount: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="IDR")
    status: Mapped[str] = mapped_column(String(20), default=OrderStatus.DRAFT)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    terms_accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    terms_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    canceled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    canceled_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    cancellation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    @property
    def allowed_actions(self) -> list[str]:
        return order_allowed_actions(self.status, self.canceled_by)


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id"), nullable=False)
    provider: Mapped[str] = mapped_column(String(20), default="doku")
    provider_transaction_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    provider_order_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    payment_type: Mapped[str | None] = mapped_column(String(60), nullable=True)
    gross_amount: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
    payment_sequence: Mapped[int | None] = mapped_column(nullable=True)
    payment_sequence_count: Mapped[int | None] = mapped_column(nullable=True)
    currency: Mapped[str] = mapped_column(String(3), default="IDR")
    transaction_status: Mapped[str] = mapped_column(String(30), default=PaymentStatus.CREATED)
    fraud_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    signature_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    paid_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expired_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    checkout_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    channel_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    virtual_account_no: Mapped[str | None] = mapped_column(String(40), nullable=True)
    provider_reference_no: Mapped[str | None] = mapped_column(String(128), nullable=True)
    offline_receipt_number: Mapped[str | None] = mapped_column(String(100), unique=True, nullable=True)
    confirmed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    external_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payment_instructions_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    deletion_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    hidden_from_user_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    @property
    def allowed_actions(self) -> list[str]:
        return payment_allowed_actions(self.transaction_status, self.deleted_at)


class PaymentProof(Base):
    __tablename__ = "payment_proofs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    payment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("payments.id", ondelete="CASCADE"), nullable=False)
    uploaded_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(500), nullable=False, unique=True)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    file_size: Mapped[int] = mapped_column(nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class PaymentWebhookEvent(Base):
    __tablename__ = "payment_webhook_events"
    __table_args__ = (UniqueConstraint("provider", "request_id", name="uq_payment_webhook_provider_request"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    payment_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("payments.id"), nullable=True)
    provider: Mapped[str] = mapped_column(String(30), nullable=False)
    request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    event_status: Mapped[str | None] = mapped_column(String(40), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class PaymentWebhookCapture(Base):
    """Raw inbound webhook evidence, including requests that fail validation."""
    __tablename__ = "payment_webhook_captures"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    provider: Mapped[str] = mapped_column(String(30), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    headers: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    raw_body: Mapped[str] = mapped_column(Text, nullable=False)
    parsed_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    processing_status: Mapped[str] = mapped_column(String(20), nullable=False, default="received")
    processing_result: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DirectDebitBinding(Base):
    """Tokenised Direct Debit account binding; never stores account/card numbers."""
    __tablename__ = "direct_debit_bindings"
    __table_args__ = (UniqueConstraint("participant_id", "channel_code", "token_id", name="uq_direct_debit_binding_token"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    participant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("participants.id"), nullable=False)
    channel_code: Mapped[str] = mapped_column(String(40), nullable=False)
    customer_reference: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_reference_no: Mapped[str | None] = mapped_column(String(128), nullable=True)
    token_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")
    raw_response: Mapped[str | None] = mapped_column(Text, nullable=True)


class PaymentChannel(Base):
    __tablename__ = "payment_channels"
    __table_args__ = (UniqueConstraint("provider", "code", name="uq_payment_channel_provider_code"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    provider: Mapped[str] = mapped_column(String(30), nullable=False, default="doku")
    code: Mapped[str] = mapped_column(String(60), nullable=False)
    category: Mapped[str] = mapped_column(String(30), nullable=False)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    logo_url: Mapped[str | None] = mapped_column(Text(), nullable=True)
    config_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    merchant_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    sub_merchant_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    terminal_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    is_enabled: Mapped[bool] = mapped_column(nullable=False, default=False)
    sort_order: Mapped[int] = mapped_column(nullable=False, default=100)
