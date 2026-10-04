from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ShirtSizeWrite(BaseModel):
    code: str = Field(min_length=1, max_length=24, pattern=r"^[A-Za-z0-9_-]+$")
    label: str = Field(min_length=1, max_length=80)
    size_chart: dict | None = None
    active: bool = True
    sort_order: int = 0
    capacity: int = Field(ge=0)


class ShirtSizeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    event_id: UUID
    code: str
    label: str
    size_chart: dict | None
    active: bool
    sort_order: int
    capacity: int
    reserved: int
    allocated: int
    available: int


class OrderParticipantWrite(BaseModel):
    full_name: str = Field(min_length=2, max_length=255)
    birth_date: date | None = None
    guardian_name: str | None = Field(default=None, max_length=255)
    guardian_contact: str | None = Field(default=None, max_length=40)
    province_code: str | None = Field(default=None, min_length=2, max_length=10)
    regency_code: str | None = Field(default=None, min_length=4, max_length=10)
    district_code: str | None = Field(default=None, min_length=6, max_length=10)
    village_code: str | None = Field(default=None, min_length=10, max_length=10)
    activity_type: str = Field(pattern="^(CYCLING|FAMILY_WALK)$")
    shirt_size_code: str = Field(min_length=1, max_length=24)

    @model_validator(mode="after")
    def require_guardian_for_minor(self):
        if self.birth_date:
            today = date.today()
            age = today.year - self.birth_date.year - ((today.month, today.day) < (self.birth_date.month, self.birth_date.day))
            if age < 18 and (not self.guardian_name or not self.guardian_contact):
                raise ValueError("Nama dan kontak wali wajib untuk peserta di bawah 18 tahun")
        return self


class OrderParticipantsWrite(BaseModel):
    participants: list[OrderParticipantWrite] = Field(min_length=1, max_length=99)


class OrderParticipantRead(BaseModel):
    id: UUID
    participant_number: int
    full_name: str
    birth_date: date | None
    guardian_name: str | None
    guardian_contact: str | None
    province_code: str | None
    regency_code: str | None
    district_code: str | None
    village_code: str | None
    activity_type: str
    shirt_size_code: str | None
    status: str
    created_at: datetime


class HariSantriTicketRead(BaseModel):
    ticket_id: UUID
    ticket_number: str
    participant_name: str
    activity_type: str
    status: str
    qr_token: str
    issued_at: datetime


class HariSantriCheckinWrite(BaseModel):
    qr_token: str = Field(min_length=32, max_length=512)


class BazaarApplicationWrite(BaseModel):
    business_name: str = Field(min_length=2, max_length=180)
    representative_name: str = Field(min_length=2, max_length=180)
    contact_email: str = Field(min_length=6, max_length=255)
    contact_phone: str = Field(min_length=5, max_length=40)
    category: str = Field(pattern="^(food|islamic_books|halal_products|other)$")
    product_summary: str = Field(min_length=10, max_length=3000)
    stall_needs: list[str] = Field(default_factory=list, max_length=10)


class BazaarApplicationRead(BaseModel):
    id: UUID
    event_id: UUID
    business_name: str
    representative_name: str
    contact_email: str
    contact_phone: str
    category: str
    product_summary: str
    stall_needs: list[str]
    status: str
    organizer_notes: str | None
    created_at: datetime


class BazaarApplicationDecision(BaseModel):
    status: str = Field(pattern="^(under_review|approved|rejected|needs_revision)$")
    organizer_notes: str | None = Field(default=None, max_length=2000)


class VoucherCreate(BaseModel):
    participant_id: UUID
    initial_balance: int = Field(ge=0, le=10_000_000_000)


class VoucherParticipantRead(BaseModel):
    participant_id: UUID
    participant_number: int
    participant_name: str
    activity_type: str
    order_id: UUID


class VoucherScan(BaseModel):
    qr_token: str = Field(min_length=32, max_length=512)
    amount: int = Field(gt=0, le=10_000_000_000)
    request_id: str = Field(min_length=8, max_length=100)
    participant_password: str = Field(min_length=1, max_length=255)


class WalletRead(BaseModel):
    participant_id: UUID | None = None
    exhibitor_id: UUID | None = None
    balance: int


class VoucherRead(BaseModel):
    voucher_id: UUID
    participant_id: UUID
    participant_name: str
    balance: int
    initial_balance: int
    status: str
    expires_at: datetime
    qr_token: str | None = None
    issued_at: datetime


class WalletTransferRead(BaseModel):
    transfer_id: UUID
    request_id: str
    participant_id: UUID
    exhibitor_id: UUID
    amount: int
    participant_balance: int
    exhibitor_balance: int
    created_at: datetime


class WalletTransferHistoryRead(BaseModel):
    transfer_id: UUID
    participant_id: UUID
    exhibitor_id: UUID
    amount: int
    direction: str
    created_at: datetime


class WalletAdjustmentWrite(BaseModel):
    amount: int = Field(ge=-10_000_000_000, le=10_000_000_000)
    reason: str = Field(min_length=5, max_length=500)

    @model_validator(mode="after")
    def amount_must_not_be_zero(self):
        if self.amount == 0:
            raise ValueError("Penyesuaian saldo tidak boleh nol")
        return self


class SettlementCreate(BaseModel):
    amount: int = Field(gt=0, le=10_000_000_000)
    payment_reference: str | None = Field(default=None, max_length=120)
    notes: str | None = Field(default=None, max_length=1000)


class SettlementRequest(BaseModel):
    payment_reference: str | None = Field(default=None, max_length=120)
    notes: str | None = Field(default=None, max_length=1000)


class SettlementRead(BaseModel):
    settlement_id: UUID
    exhibitor_id: UUID
    amount: int
    status: str
    payment_reference: str | None
    notes: str | None
    requested_at: datetime
    confirmed_at: datetime | None


class ExhibitorWalletTargetRead(BaseModel):
    exhibitor_id: UUID
    business_name: str
    balance: int


class MyExhibitorRead(ExhibitorWalletTargetRead):
    status: str


class SettlementAdminRead(SettlementRead):
    business_name: str


class SettlementReconciliationRead(BaseModel):
    exhibitor_id: UUID
    business_name: str
    total_credits: int
    pending_settlement: int
    confirmed_settlement: int
    wallet_balance: int
    expected_balance: int
    status: str


class HariSantriAuditLogRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    actor_user_id: UUID | None
    actor_name: str | None = None
    action: str
    entity_type: str
    entity_id: UUID | None
    payload: dict
    created_at: datetime
