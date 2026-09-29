from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ValidationException
from app.modules.regions.models import AdministrativeRegion
from app.modules.regions.schemas import RegionLevel, RegionRead


class RegionService:
    LEVEL_PARENT = {
        "province": None,
        "regency": "province",
        "district": "regency",
        "village": "district",
    }

    @staticmethod
    async def list_children(
        db: AsyncSession, level: RegionLevel, parent_code: str | None = None
    ) -> list[RegionRead]:
        expected_parent_level = RegionService.LEVEL_PARENT[level]
        if expected_parent_level is None and parent_code:
            raise ValidationException("REGION_PARENT_NOT_ALLOWED", "Provinsi tidak memiliki parent")
        if expected_parent_level is not None and not parent_code:
            raise ValidationException("REGION_PARENT_REQUIRED", "Parent wilayah wajib dipilih")

        statement = select(AdministrativeRegion).where(AdministrativeRegion.level == level)
        if parent_code:
            statement = statement.where(AdministrativeRegion.parent_code == parent_code)
        statement = statement.order_by(AdministrativeRegion.name)
        rows = (await db.execute(statement)).scalars().all()
        return [RegionRead.model_validate(row) for row in rows]

    @staticmethod
    async def validate_chain(
        db: AsyncSession,
        province_code: str,
        regency_code: str,
        district_code: str,
        village_code: str,
    ) -> None:
        codes = [province_code, regency_code, district_code, village_code]
        rows = list(
            (await db.execute(select(AdministrativeRegion).where(AdministrativeRegion.code.in_(codes))))
            .scalars()
            .all()
        )
        by_code = {row.code: row for row in rows}
        if len(by_code) != len(set(codes)):
            raise ValidationException("INVALID_REGION_CODE", "Kode wilayah tidak ditemukan")
        expected = [
            (province_code, "province", None),
            (regency_code, "regency", province_code),
            (district_code, "district", regency_code),
            (village_code, "village", district_code),
        ]
        for code, level, parent_code in expected:
            region = by_code[code]
            if region.level != level or region.parent_code != parent_code:
                raise ValidationException("INVALID_REGION_HIERARCHY", "Urutan kode wilayah tidak sesuai")
