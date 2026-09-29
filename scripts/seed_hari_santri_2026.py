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


async def seed_event() -> None:
    timezone = ZoneInfo("Asia/Jakarta")
    start_at = datetime(2026, 10, 25, 0, 0, tzinfo=timezone)
    end_at = datetime(2026, 10, 26, 0, 0, tzinfo=timezone)
    async with AsyncSessionFactory() as session:
        event = (await session.execute(select(Event).where(Event.slug == "hari-santri-2026"))).scalar_one_or_none()
        if event:
            print(f"Hari Santri 2026 event already exists: {event.id}")
            return
        session.add(Event(
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
        ))
        await session.commit()
        print("Created Hari Santri 2026 draft event. Prices, capacity, activities and routes remain organizer-managed.")


async def main() -> None:
    try:
        await seed_event()
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
