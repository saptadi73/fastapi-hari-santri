from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ProductWrite(BaseModel):
    code: str = Field(min_length=2, max_length=60)
    name: str = Field(min_length=2, max_length=180)
    description: str | None = None
    product_type: str = Field(pattern="^(delegate|exhibitor|additional|hari_santri_package)$")
    price: float = Field(ge=0)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    max_quantity: int | None = Field(default=None, ge=1)
    metadata_json: dict = Field(default_factory=dict)
    is_active: bool = True


class ProductRead(ProductWrite):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    event_id: UUID


class AdditionalProductAvailability(ProductRead):
    purchase_status: str
    is_purchasable: bool
    existing_order_id: UUID | None = None
    registration_id: UUID | None = None
    reason: str | None = None


class CartItemWrite(BaseModel):
    product_id: UUID
    quantity: int = Field(ge=1, le=99)


class CartItemRead(BaseModel):
    id: UUID
    product_id: UUID
    code: str
    name: str
    product_type: str
    quantity: int
    unit_price: float
    currency: str
    line_total: float


class CartRead(BaseModel):
    id: UUID
    event_id: UUID
    items: list[CartItemRead]
    subtotal: float
    currency: str | None = None


class CheckoutRead(BaseModel):
    order_id: UUID
    order_number: str
    total_amount: float
    currency: str
    status: str
    item_count: int
    created_at: datetime
    order_kind: str = "legacy"


class CheckoutConsent(BaseModel):
    terms_accepted: bool = False
