"""PostgreSQL-backed rate limiter shared by all API workers."""

from __future__ import annotations

import math
import time
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.modules.hari_santri.models import VoucherScanRateLimit


def voucher_scan_rate_limit_key(*, user_id: object | None, client_host: str | None) -> str:
    """Build a key from the authenticated account; never include QR data."""

    return f"user:{user_id}" if user_id is not None else f"ip:{client_host or 'unknown'}"


def build_rate_limit_upsert(*, key: str, window_start: int, max_requests: int, now: datetime):
    statement = pg_insert(VoucherScanRateLimit).values(
        subject_key=key,
        window_start=window_start,
        request_count=1,
        updated_at=now,
    )
    return statement.on_conflict_do_update(
        index_elements=[VoucherScanRateLimit.subject_key, VoucherScanRateLimit.window_start],
        set_={
            "request_count": VoucherScanRateLimit.request_count + 1,
            "updated_at": now,
        },
        where=VoucherScanRateLimit.request_count < max_requests,
    ).returning(VoucherScanRateLimit.request_count)


async def enforce_voucher_scan_rate_limit(db: AsyncSession, key: str) -> None:
    """Atomically consume one request from this account's current time bucket."""

    settings = get_settings()
    window_seconds = max(1, settings.VOUCHER_SCAN_RATE_LIMIT_WINDOW_SECONDS)
    max_requests = max(1, settings.VOUCHER_SCAN_RATE_LIMIT_PER_MINUTE)
    now = time.time()
    now_datetime = datetime.fromtimestamp(now, tz=timezone.utc)
    window_start = int(now // window_seconds) * window_seconds

    await db.execute(
        delete(VoucherScanRateLimit).where(
            VoucherScanRateLimit.subject_key == key,
            VoucherScanRateLimit.window_start < window_start,
        )
    )
    result = await db.execute(
        build_rate_limit_upsert(
            key=key,
            window_start=window_start,
            max_requests=max_requests,
            now=now_datetime,
        )
    )
    accepted_count = result.scalar_one_or_none()
    await db.commit()

    if accepted_count is None:
        retry_after = max(1, math.ceil(window_start + window_seconds - now))
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Terlalu banyak percobaan pembayaran voucher. Coba lagi nanti.",
            headers={"Retry-After": str(retry_after)},
        )
