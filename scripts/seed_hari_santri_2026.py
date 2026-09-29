"""Create the confirmed Hari Santri event record without inventing business data."""
import asyncio
from datetime import datetime
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.core.database import AsyncSessionFactory, engine
from app.modules.events.models import Event, EventStatus
from app.modules.store.models import Product
from app.modules.iwbif import models as iwbif_models


DEFAULT_PRODUCTS = (
    {
        "code": "HS26-CYCLING",
        "name": "Paket Sepeda Sehat",
        "description": "Paket kegiatan Sepeda Sehat Hari Santri 2026.",
        "activity_type": "CYCLING",
    },
    {
        "code": "HS26-FAMILY-WALK",
        "name": "Paket Jalan Sehat Keluarga",
        "description": "Paket kegiatan Jalan Sehat Keluarga Hari Santri 2026.",
        "activity_type": "FAMILY_WALK",
    },
)


async def seed_event() -> None:
    timezone = ZoneInfo("Asia/Jakarta")
    start_at = datetime(2026, 10, 25, 0, 0, tzinfo=timezone)
    end_at = datetime(2026, 10, 26, 0, 0, tzinfo=timezone)
    async with AsyncSessionFactory() as session:
        event = (await session.execute(select(Event).where(Event.slug == "hari-santri-2026"))).scalar_one_or_none()
        if not event:
            event = Event(
                name="Hari Santri 2026 — Sepeda Sehat & Jalan Sehat Keluarga",
                slug="hari-santri-2026",
                description="Perayaan Hari Santri 2026 oleh MWC NU Tarumajaya.",
                venue_name="Summarecon Crown Gading",
                venue_address="Tarumajaya, Bekasi",
                timezone="Asia/Jakarta",
                start_at=start_at,
                end_at=end_at,
                capacity=0,
                status=EventStatus.DRAFT,
            )
            session.add(event)
            await session.flush()
        for product_data in DEFAULT_PRODUCTS:
            product = (await session.execute(
                select(Product).where(Product.event_id == event.id, Product.code == product_data["code"])
            )).scalar_one_or_none()
            if product is None:
                session.add(Product(
                    event_id=event.id,
                    code=product_data["code"],
                    name=product_data["name"],
                    description=product_data["description"],
                    product_type="hari_santri_package",
                    price=0,
                    currency="IDR",
                    max_quantity=1,
                    metadata_json={
                        "activity_type": product_data["activity_type"],
                        "min_participants": 1,
                        "max_participants": 20,
                        "capacity_people": 0,
                        "seed_status": "needs_admin_configuration",
                    },
                    is_active=False,
                ))
        await session.commit()
        print("Hari Santri event and default Cycling/Family Walk package placeholders are ready; prices and activation remain organizer-managed.")


async def main() -> None:
    try:
        await seed_event()
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
