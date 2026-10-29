"""Seed or update the Harlah NU ke-101 participant packages.

This script is idempotent and keeps prices server-side. Run it only after
reviewing the target database and event slug.
"""
import asyncio
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.core.database import AsyncSessionFactory, engine
from app.modules.events.models import Event
from app.modules.store.models import Product


PACKAGES = (
    {
        "code": "HNU101-CYCLING",
        "name": "Paket Sepeda Santai",
        "description": "Tiket Sepeda Santai termasuk desain kaos kegiatan sesuai ukuran yang dipilih peserta.",
        "activity_type": "CYCLING",
        "price": 75000,
    },
    {
        "code": "HNU101-FAMILY-WALK",
        "name": "Paket Jalan Sehat Keluarga",
        "description": "Tiket Jalan Sehat Keluarga termasuk desain kaos kegiatan sesuai ukuran yang dipilih peserta.",
        "activity_type": "FAMILY_WALK",
        "price": 25000,
    },
)


async def seed_packages() -> None:
    async with AsyncSessionFactory() as session:
        event = (await session.execute(select(Event).where(Event.slug == "harlah-nu-101"))).scalar_one_or_none()
        if event is None:
            raise RuntimeError("Event harlah-nu-101 belum tersedia. Buat event terlebih dahulu.")

        for package_data in PACKAGES:
            product = (await session.execute(
                select(Product).where(Product.event_id == event.id, Product.code == package_data["code"])
            )).scalar_one_or_none()
            metadata = {
                "activity_type": package_data["activity_type"],
                "min_participants": 1,
                "max_participants": 20,
                "shirt_design": "Harlah NU ke-101",
            }
            if product is None:
                product = Product(
                    event_id=event.id,
                    code=package_data["code"],
                    name=package_data["name"],
                    description=package_data["description"],
                    product_type="hari_santri_package",
                    price=package_data["price"],
                    currency="IDR",
                    max_quantity=1,
                    metadata_json=metadata,
                    is_active=True,
                )
                session.add(product)
            else:
                product.name = package_data["name"]
                product.description = package_data["description"]
                product.price = package_data["price"]
                product.currency = "IDR"
                product.metadata_json = {**(product.metadata_json or {}), **metadata}
                product.is_active = True

        await session.commit()
        print("Paket Harlah NU ke-101 diperbarui: Sepeda Santai Rp75.000, Jalan Sehat Keluarga Rp25.000.")


async def main() -> None:
    try:
        await seed_packages()
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
