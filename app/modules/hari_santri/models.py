import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, CheckConstraint, Date, DateTime, ForeignKey, Integer, JSON, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ShirtSize(Base):
    __tablename__ = "shirt_sizes"
    __table_args__ = (UniqueConstraint("event_id", "code", name="uq_shirt_size_event_code"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), nullable=False)
    code: Mapped[str] = mapped_column(String(24), nullable=False)
    label: Mapped[str] = mapped_column(String(80), nullable=False)
    size_chart: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class ShirtInventory(Base):
    __tablename__ = "shirt_inventory"
    __table_args__ = (
        UniqueConstraint("event_id", "size_id", name="uq_shirt_inventory_event_size"),
        CheckConstraint(
            "capacity >= 0 AND reserved >= 0 AND allocated >= 0 AND reserved + allocated <= capacity",
            name="ck_shirt_inventory_capacity",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), nullable=False)
    size_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("shirt_sizes.id", ondelete="CASCADE"), nullable=False)
    capacity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reserved: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    allocated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class OrderParticipant(Base):
    __tablename__ = "order_participants"
    __table_args__ = (
        UniqueConstraint("order_id", "participant_number", name="uq_order_participant_number"),
        CheckConstraint("participant_number > 0", name="ck_order_participant_number_positive"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), nullable=False)
    participant_number: Mapped[int] = mapped_column(Integer, nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    birth_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    guardian_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    guardian_contact: Mapped[str | None] = mapped_column(String(40), nullable=True)
    province_code: Mapped[str | None] = mapped_column(String(10), ForeignKey("administrative_regions.code", ondelete="RESTRICT"), nullable=True)
    regency_code: Mapped[str | None] = mapped_column(String(10), ForeignKey("administrative_regions.code", ondelete="RESTRICT"), nullable=True)
    district_code: Mapped[str | None] = mapped_column(String(10), ForeignKey("administrative_regions.code", ondelete="RESTRICT"), nullable=True)
    village_code: Mapped[str | None] = mapped_column(String(10), ForeignKey("administrative_regions.code", ondelete="RESTRICT"), nullable=True)
    activity_type: Mapped[str] = mapped_column(String(30), nullable=False)
    shirt_size_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("shirt_sizes.id", ondelete="SET NULL"), nullable=True)
    shirt_size_code_snapshot: Mapped[str | None] = mapped_column(String(24), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="reserved")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class HariSantriPayment(Base):
    __tablename__ = "hari_santri_payments"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, unique=True)
    payment_portal_id: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    payment_no: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    reference_id: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    payment_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    amount: Mapped[float] = mapped_column(Numeric(18, 0), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="IDR")
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="creating")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class HariSantriCallbackEvent(Base):
    __tablename__ = "hari_santri_callback_events"
    __table_args__ = (UniqueConstraint("event_id", name="uq_hari_santri_callback_event_id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    event_id: Mapped[str] = mapped_column(String(150), nullable=False, unique=True)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    processing_status: Mapped[str] = mapped_column(String(20), nullable=False, default="processed")
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class HariSantriTicket(Base):
    __tablename__ = "hari_santri_tickets"
    __table_args__ = (
        UniqueConstraint("participant_id", name="uq_hari_santri_ticket_participant"),
        UniqueConstraint("ticket_number", name="uq_hari_santri_ticket_number"),
        UniqueConstraint("qr_token_hash", name="uq_hari_santri_ticket_token_hash"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    participant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("order_participants.id", ondelete="CASCADE"), nullable=False)
    ticket_number: Mapped[str] = mapped_column(String(40), nullable=False)
    qr_token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    checked_in_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class HariSantriCheckin(Base):
    __tablename__ = "hari_santri_checkins"
    __table_args__ = (UniqueConstraint("ticket_id", name="uq_hari_santri_checkin_ticket"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    ticket_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hari_santri_tickets.id", ondelete="CASCADE"), nullable=False)
    scanned_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    scanned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    result: Mapped[str] = mapped_column(String(20), nullable=False, default="accepted")


class BazaarApplication(Base):
    __tablename__ = "bazaar_applications"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    business_name: Mapped[str] = mapped_column(String(180), nullable=False)
    representative_name: Mapped[str] = mapped_column(String(180), nullable=False)
    contact_email: Mapped[str] = mapped_column(String(255), nullable=False)
    contact_phone: Mapped[str] = mapped_column(String(40), nullable=False)
    category: Mapped[str] = mapped_column(String(30), nullable=False)
    product_summary: Mapped[str] = mapped_column(Text, nullable=False)
    stall_needs: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="submitted")
    organizer_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class ParticipantWallet(Base):
    __tablename__ = "participant_wallets"
    __table_args__ = (CheckConstraint("balance >= 0", name="ck_participant_wallet_balance_nonnegative"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    participant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("order_participants.id", ondelete="CASCADE"), nullable=False)
    balance: Mapped[Decimal] = mapped_column(Numeric(18, 0), nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class ExhibitorWallet(Base):
    __tablename__ = "exhibitor_wallets"
    __table_args__ = (CheckConstraint("balance >= 0", name="ck_exhibitor_wallet_balance_nonnegative"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    exhibitor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bazaar_applications.id", ondelete="CASCADE"), nullable=False, unique=True)
    balance: Mapped[Decimal] = mapped_column(Numeric(18, 0), nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class ParticipantVoucher(Base):
    __tablename__ = "participant_vouchers"
    __table_args__ = (UniqueConstraint("qr_token_hash", name="uq_participant_voucher_qr_hash"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    participant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("order_participants.id", ondelete="CASCADE"), nullable=False)
    wallet_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("participant_wallets.id", ondelete="CASCADE"), nullable=False, unique=True)
    qr_token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    initial_balance: Mapped[Decimal] = mapped_column(Numeric(18, 0), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    issued_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class WalletTransfer(Base):
    __tablename__ = "wallet_transfers"
    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_wallet_transfer_amount_positive"),
        UniqueConstraint("request_id", name="uq_wallet_transfer_request_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    request_id: Mapped[str] = mapped_column(String(100), nullable=False)
    voucher_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("participant_vouchers.id", ondelete="RESTRICT"), nullable=False)
    participant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("order_participants.id", ondelete="RESTRICT"), nullable=False)
    exhibitor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bazaar_applications.id", ondelete="RESTRICT"), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 0), nullable=False)
    scanned_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class WalletAdjustment(Base):
    __tablename__ = "wallet_adjustments"
    __table_args__ = (CheckConstraint("amount <> 0", name="ck_wallet_adjustment_amount_nonzero"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    voucher_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("participant_vouchers.id", ondelete="RESTRICT"), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 0), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    adjusted_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class ExhibitorSettlement(Base):
    __tablename__ = "exhibitor_settlements"
    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_exhibitor_settlement_amount_positive"),
        CheckConstraint("status IN ('pending', 'confirmed')", name="ck_exhibitor_settlement_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    exhibitor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bazaar_applications.id", ondelete="RESTRICT"), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 0), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    payment_reference: Mapped[str | None] = mapped_column(String(120), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class HariSantriAuditLog(Base):
    __tablename__ = "hari_santri_audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(80), nullable=False)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class VoucherScanRateLimit(Base):
    __tablename__ = "voucher_scan_rate_limits"
    __table_args__ = (CheckConstraint("request_count > 0", name="ck_voucher_scan_rate_limits_request_count_positive"),)

    subject_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    window_start: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    request_count: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
