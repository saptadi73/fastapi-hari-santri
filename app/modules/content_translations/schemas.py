from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TranslationWrite(BaseModel):
    fields: dict[str, Any] = Field(min_length=1)

    @field_validator("fields", mode="before")
    @classmethod
    def omit_empty_optional_fields(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        return {
            key: item
            for key, item in value.items()
            if item is not None and not (isinstance(item, str) and not item.strip())
        }


class TranslationRead(BaseModel):
    id: UUID
    entity_type: str
    entity_id: UUID
    locale: Literal["id", "en", "zh-CN"]
    fields: dict[str, Any]
    created_by: UUID | None
    updated_by: UUID | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
