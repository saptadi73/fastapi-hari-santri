from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator
from typing import Literal

class UserBase(BaseModel):
    email: str = Field(min_length=6, max_length=255)
    full_name: str | None = Field(default=None, min_length=2, max_length=255)
    phone: str | None = Field(default=None, max_length=40)
    country: str | None = Field(default=None, max_length=100)
    province_code: str | None = Field(default=None, min_length=2, max_length=10)
    regency_code: str | None = Field(default=None, min_length=4, max_length=10)
    district_code: str | None = Field(default=None, min_length=6, max_length=10)
    village_code: str | None = Field(default=None, min_length=10, max_length=10)
    preferred_locale: Literal["id", "en"] = "id"


class UserCreate(UserBase):
    password: str = Field(min_length=8, max_length=128)
    country: str | None = Field(default=None, min_length=2, max_length=100)
    phone: str = Field(min_length=5, max_length=40)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        if "@" not in value or "." not in value.split("@")[-1]:
            raise ValueError("Email tidak valid")
        return value.lower()


class UserLogin(BaseModel):
    email: str = Field(min_length=6, max_length=255)
    password: str = Field(min_length=8, max_length=128)

    @field_validator("email")
    @classmethod
    def validate_email_login(cls, value: str) -> str:
        if "@" not in value or "." not in value.split("@")[-1]:
            raise ValueError("Email tidak valid")
        return value.lower()


class UserUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=2, max_length=255)
    phone: str | None = Field(default=None, max_length=40)
    preferred_locale: Literal["id", "en"] | None = None


class ChangePassword(BaseModel):
    current_password: str = Field(min_length=8, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)
    confirm_password: str = Field(min_length=8, max_length=128)


class UserRead(UserBase):
    id: UUID
    status: str
    registration_status: str
    role: str
    is_email_verified: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class UserProfileSnapshot(BaseModel):
    id: UUID
    user_id: UUID
    full_name: str
    organization_name: str | None = None
    biography: str | None = None
    profile_photo_url: str | None = None

    model_config = ConfigDict(from_attributes=True)


class AdminUserCreate(UserCreate):
    full_name: str | None = Field(default=None, min_length=2, max_length=255)
    role: Literal["participant", "organizer", "admin"] = "participant"
    status: Literal["active", "inactive", "suspended"] = "active"
    is_email_verified: bool = False


class AdminUserUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=2, max_length=255)
    phone: str | None = Field(default=None, min_length=5, max_length=40)
    country: str | None = Field(default=None, min_length=2, max_length=100)
    role: Literal["participant", "organizer", "admin"] | None = None
    status: Literal["active", "inactive", "suspended"] | None = None
    is_email_verified: bool | None = None
