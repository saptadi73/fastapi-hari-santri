import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from sqlalchemy.dialects import postgresql

from app.core.rate_limit import (
    build_rate_limit_upsert,
    enforce_voucher_scan_rate_limit,
    voucher_scan_rate_limit_key,
)


class VoucherScanRateLimitTests(unittest.IsolatedAsyncioTestCase):
    def test_database_upsert_enforces_limit_atomically(self):
        statement = build_rate_limit_upsert(
            key="user:account-1",
            window_start=120,
            max_requests=30,
            now=datetime.now(timezone.utc),
        )
        sql = str(statement.compile(dialect=postgresql.dialect()))

        self.assertIn("ON CONFLICT (subject_key, window_start) DO UPDATE", sql)
        self.assertIn("request_count <", sql)
        self.assertIn("RETURNING", sql)

    async def test_request_is_committed_before_endpoint_business_work(self):
        accepted = SimpleNamespace(scalar_one_or_none=lambda: 1)
        db = SimpleNamespace(execute=AsyncMock(side_effect=[None, accepted]), commit=AsyncMock())
        settings = SimpleNamespace(
            VOUCHER_SCAN_RATE_LIMIT_PER_MINUTE=30,
            VOUCHER_SCAN_RATE_LIMIT_WINDOW_SECONDS=60,
        )

        with patch("app.core.rate_limit.get_settings", return_value=settings), patch("app.core.rate_limit.time.time", return_value=112.0):
            await enforce_voucher_scan_rate_limit(db, "user:account-1")

        self.assertEqual(2, db.execute.await_count)
        db.commit.assert_awaited_once()

    async def test_limit_response_includes_seconds_until_shared_window_resets(self):
        denied = SimpleNamespace(scalar_one_or_none=lambda: None)
        db = SimpleNamespace(execute=AsyncMock(side_effect=[None, denied]), commit=AsyncMock())
        settings = SimpleNamespace(
            VOUCHER_SCAN_RATE_LIMIT_PER_MINUTE=1,
            VOUCHER_SCAN_RATE_LIMIT_WINDOW_SECONDS=60,
        )

        with patch("app.core.rate_limit.get_settings", return_value=settings), patch("app.core.rate_limit.time.time", return_value=112.0):
            with self.assertRaises(HTTPException) as caught:
                await enforce_voucher_scan_rate_limit(db, "user:account-1")

        self.assertEqual(429, caught.exception.status_code)
        self.assertEqual("8", caught.exception.headers["Retry-After"])
        db.commit.assert_awaited_once()

    def test_rate_limit_key_uses_account_and_never_qr_token(self):
        qr_token = "sensitive-qr-token"
        key = voucher_scan_rate_limit_key(user_id="participant-1", client_host="127.0.0.1")

        self.assertEqual("user:participant-1", key)
        self.assertNotIn(qr_token, key)


if __name__ == "__main__":
    unittest.main()
