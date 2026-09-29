from typing import Literal

from pydantic import BaseModel, ConfigDict


RegionLevel = Literal["province", "regency", "district", "village"]


class RegionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    name: str
    level: RegionLevel
    parent_code: str | None
