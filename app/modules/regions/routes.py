from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db_session
from app.modules.regions.schemas import RegionLevel
from app.modules.regions.service import RegionService
from app.support.responses import success_response

router = APIRouter(prefix="/regions", tags=["regions"])


@router.get("")
async def list_regions(
    request: Request,
    level: Annotated[RegionLevel, Query()],
    parent_code: str | None = Query(default=None, min_length=2, max_length=10),
    db: AsyncSession = Depends(get_db_session),
):
    regions = await RegionService.list_children(db, level, parent_code)
    return success_response("Daftar wilayah ditemukan", data=regions, request=request)
