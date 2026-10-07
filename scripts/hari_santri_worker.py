"""One-shot maintenance worker; schedule it externally (for example, each minute)."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import uuid

from app.core.database import AsyncSessionFactory
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
    args = parser.parse_args()
    request_id = (args.request_id or str(uuid.uuid4()))[:100]
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    result = asyncio.run(run_once(batch_size=max(1, min(args.batch_size, 500)), request_id=request_id))
    print(json.dumps(result, default=str))


if __name__ == "__main__":
    main()
