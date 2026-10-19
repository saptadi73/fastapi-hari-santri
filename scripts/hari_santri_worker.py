"""One-shot maintenance worker; schedule it externally (for example, each minute)."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import signal
import uuid

from app.core.database import AsyncSessionFactory
# Import every model module before opening a session.  The API imports these
# through its routers, while this standalone worker does not; without the
# registry SQLAlchemy cannot resolve cross-module foreign keys such as orders
# -> registrations when it first builds a query.
from app.modules.admin_content import models as _admin_content_models  # noqa: F401
from app.modules.business_matching import models as _business_matching_models  # noqa: F401
from app.modules.check_ins.models import CheckIn  # noqa: F401
from app.modules.committee import models as _committee_models  # noqa: F401
from app.modules.email_notifications import models as _email_notification_models  # noqa: F401
from app.modules.events.models import Event  # noqa: F401
from app.modules.iwbif import models as _iwbif_models  # noqa: F401
from app.modules.participants.models import ParticipantProfile  # noqa: F401
from app.modules.regions import models as _regions_models  # noqa: F401
from app.modules.registrations.models import Registration  # noqa: F401
from app.modules.sessions.models import EventSession  # noqa: F401
from app.modules.speakers.models import EventSpeaker, Speaker  # noqa: F401
from app.modules.tickets.models import QRToken, Ticket  # noqa: F401
from app.modules.users.models import User  # noqa: F401
from app.modules.payments.models import (  # noqa: F401
    DirectDebitBinding,
    Order,
    Payment,
    PaymentChannel,
    PaymentWebhookEvent,
)
from app.modules.hari_santri import models as _hari_santri_models  # noqa: F401
from app.modules.hari_santri.service import HariSantriService


async def run_once(*, batch_size: int, request_id: str) -> dict:
    totals = {"request_id": request_id, "expired_orders": 0, "inventory_mismatch_orders": [], "callbacks": {"claimed": 0, "completed": 0, "retry_scheduled": 0, "dead": 0}}
    async with AsyncSessionFactory() as db:
        while True:
            result = await HariSantriService.expire_due_hari_santri_orders(
                db, request_id=request_id, batch_size=batch_size
            )
            totals["expired_orders"] += result["expired_orders"]
            totals["inventory_mismatch_orders"].extend(result["inventory_mismatch_orders"])
            if result["expired_orders"] < batch_size:
                break
        while True:
            result = await HariSantriService.process_callback_retry_outbox(db, batch_size=batch_size, worker_request_id=request_id)
            for key in totals["callbacks"]:
                totals["callbacks"][key] += result[key]
            if result["claimed"] < batch_size:
                break
    return totals


def main() -> None:
    parser = argparse.ArgumentParser(description="Expire Hari Santri reservations and retry verified payment callbacks")
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--request-id", default=None, help="Optional trace ID; generated when omitted")
    parser.add_argument("--watch", action="store_true", help="Run continuously until SIGTERM/SIGINT")
    parser.add_argument("--interval-seconds", type=float, default=5, help="Delay between watch cycles")
    args = parser.parse_args()
    request_id = (args.request_id or str(uuid.uuid4()))[:100]
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    batch_size = max(1, min(args.batch_size, 500))

    async def run() -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stop.set)
            except NotImplementedError:
                pass
        while True:
            cycle_request_id = str(uuid.uuid4())[:100] if args.watch else request_id
            result = await run_once(batch_size=batch_size, request_id=cycle_request_id)
            print(json.dumps(result, default=str), flush=True)
            if not args.watch:
                return
            try:
                await asyncio.wait_for(stop.wait(), timeout=max(1, args.interval_seconds))
            except asyncio.TimeoutError:
                continue
            return

    asyncio.run(run())


if __name__ == "__main__":
    main()
