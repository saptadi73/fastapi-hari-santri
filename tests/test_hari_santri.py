import asyncio
import base64
import hashlib
import hmac
import json
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

from pydantic import ValidationError

from app.core.exceptions import AppException, ConflictException, ValidationException
from app.modules.hari_santri.payment_portal import PaymentPortalClient
from app.modules.hari_santri.schemas import OrderParticipantWrite
from app.modules.hari_santri.service import HariSantriService
from app.modules.payments.service import PaymentService
from app.modules.store.service import StoreService


class HariSantriContractTests(unittest.TestCase):
    def test_payment_portal_production_requires_https_and_callback_secret(self):
        settings = SimpleNamespace(
            PAYMENT_PORTAL_BASE_URL="https://pay.example.test",
            PAYMENT_PORTAL_CLIENT_ID="client-id",
            PAYMENT_PORTAL_CLIENT_SECRET="client-secret",
            PAYMENT_PORTAL_CALLBACK_SECRET="callback-secret",
            PAYMENT_PORTAL_RETURN_URL="https://event.example.test/pembayaran/hasil",
            PAYMENT_PORTAL_SERVICE_CODE="HARI_SANTRI_2026",
            APP_ENV="production",
            PUBLIC_BASE_URL="https://api.example.test",
        )
        with patch("app.modules.hari_santri.payment_portal.get_settings", return_value=settings):
            self.assertIs(settings, PaymentPortalClient._settings())

        settings.PAYMENT_PORTAL_RETURN_URL = "http://event.example.test/pembayaran/hasil"
        with patch("app.modules.hari_santri.payment_portal.get_settings", return_value=settings):
            with self.assertRaises(AppException) as caught:
                PaymentPortalClient._settings()
        self.assertEqual("PAYMENT_PORTAL_NOT_CONFIGURED", caught.exception.code)

        settings.PAYMENT_PORTAL_RETURN_URL = "https://event.example.test/pembayaran/hasil"
        settings.PAYMENT_PORTAL_CALLBACK_SECRET = ""
        with patch("app.modules.hari_santri.payment_portal.get_settings", return_value=settings):
            with self.assertRaises(AppException) as caught:
                PaymentPortalClient._settings()
        self.assertEqual("PAYMENT_PORTAL_NOT_CONFIGURED", caught.exception.code)

    def test_payment_portal_callback_verifies_raw_body_signature(self):
        timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        payload = {
            "event_id": "evt-1",
            "event_type": "payment.paid",
            "data": {"reference_id": "HS-1", "amount": 10000, "currency": "IDR"},
        }
        raw_body = json.dumps(payload, separators=(",", ":")).encode()
        secret = "callback-secret"
        signature = base64.b64encode(hmac.new(
            secret.encode(), timestamp.encode() + b"." + raw_body, hashlib.sha256,
        ).digest()).decode()
        settings = SimpleNamespace(
            PAYMENT_PORTAL_CALLBACK_SECRET=secret,
            PAYMENT_PORTAL_CALLBACK_TOLERANCE_SECONDS=300,
        )

        with patch("app.modules.hari_santri.payment_portal.get_settings", return_value=settings):
            verified = PaymentPortalClient.verify_callback(raw_body, "evt-1", timestamp, signature)
            self.assertEqual("payment.paid", verified["event_type"])
            with self.assertRaises(AppException):
                PaymentPortalClient.verify_callback(raw_body + b" ", "evt-1", timestamp, signature)

    def test_payment_portal_callback_rejects_stale_timestamp(self):
        timestamp = "2000-01-01T00:00:00Z"
        raw_body = json.dumps({"event_id": "evt-old", "event_type": "payment.paid", "data": {}}).encode()
        secret = "callback-secret"
        signature = base64.b64encode(hmac.new(
            secret.encode(), timestamp.encode() + b"." + raw_body, hashlib.sha256,
        ).digest()).decode()
        settings = SimpleNamespace(PAYMENT_PORTAL_CALLBACK_SECRET=secret, PAYMENT_PORTAL_CALLBACK_TOLERANCE_SECONDS=300)
        with patch("app.modules.hari_santri.payment_portal.get_settings", return_value=settings):
            with self.assertRaises(AppException) as caught:
                PaymentPortalClient.verify_callback(raw_body, "evt-old", timestamp, signature)
        self.assertEqual("INVALID_PAYMENT_PORTAL_CALLBACK", caught.exception.code)

    def test_ticket_token_is_deterministic_and_signed(self):
        with patch("app.modules.hari_santri.service.get_settings", return_value=SimpleNamespace(APP_SECRET_KEY="app-secret")):
            ticket_id = uuid4()
            token = HariSantriService._ticket_token(ticket_id)
            self.assertEqual(token, HariSantriService._ticket_token(ticket_id))
            encoded = token.split(".", 1)[1]
            expected = base64.urlsafe_b64encode(hmac.new(b"app-secret", ticket_id.bytes, hashlib.sha256).digest()).decode().rstrip("=")
            self.assertEqual(expected, encoded)

    def test_participant_requires_guardian_for_minor(self):
        with self.assertRaises(ValidationError):
            OrderParticipantWrite(
                full_name="Peserta Anak",
                birth_date="2020-01-01",
                activity_type="FAMILY_WALK",
                shirt_size_code="KIDS_S",
            )
        participant = OrderParticipantWrite(
            full_name="Peserta Anak",
            birth_date="2020-01-01",
            guardian_name="Orang Tua",
            guardian_contact="08123456789",
            activity_type="FAMILY_WALK",
            shirt_size_code="KIDS_S",
        )
        self.assertEqual("FAMILY_WALK", participant.activity_type)


class HariSantriPaymentBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_legacy_direct_gateway_rejects_hari_santri_order(self):
        db = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(slug="hari-santri-2026")))
        order = SimpleNamespace(event_id=uuid4())
        with self.assertRaises(ConflictException) as caught:
            await PaymentService._reject_direct_gateway_for_hari_santri(db, order)
        self.assertEqual("PAYMENT_PORTAL_REQUIRED", caught.exception.code)

    async def test_hari_santri_order_requires_terms_acceptance_before_checkout(self):
        event_id = uuid4()
        cart_result = SimpleNamespace(scalar_one_or_none=lambda: SimpleNamespace(id=uuid4()))
        rows_result = SimpleNamespace(all=lambda: [(SimpleNamespace(quantity=1), SimpleNamespace(
            product_type="hari_santri_package",
            metadata_json={"activity_type": "FAMILY_WALK"},
        ))])
        db = AsyncMock()
        db.execute.side_effect = [None, cart_result, rows_result]
        db.get.return_value = SimpleNamespace(slug="hari-santri-2026")
        with self.assertRaises(ValidationException) as caught:
            await StoreService.checkout(db, uuid4(), event_id)
        self.assertEqual("TERMS_ACCEPTANCE_REQUIRED", caught.exception.code)

    async def test_checkout_holds_order_lock_and_uses_stable_local_idempotency_key(self):
        from decimal import Decimal

        from app.modules.hari_santri.models import HariSantriPayment

        order_id, user_id, event_id = uuid4(), uuid4(), uuid4()
        order = SimpleNamespace(
            id=order_id, user_id=user_id, event_id=event_id, order_number="HS-LOCK-1",
            status="pending", total_amount=Decimal("125000"), currency="IDR", expires_at=None,
        )

        class Result:
            def __init__(self, value=None, rows=None):
                self.value = value
                self.rows = rows or []

            def scalar_one_or_none(self):
                return self.value

            def scalars(self):
                return self

            def all(self):
                return self.rows

        db = SimpleNamespace(
            execute=AsyncMock(side_effect=[
                Result(value=order), Result(value=uuid4()),
                Result(rows=[SimpleNamespace(product_code="FAMILY_WALK")]), Result(value=None),
            ]),
            get=AsyncMock(side_effect=[
                SimpleNamespace(id=event_id, slug="hari-santri-2026", name="Hari Santri 2026"),
                SimpleNamespace(email="person@example.test", full_name="Test User"),
            ]),
            add=Mock(),
            flush=AsyncMock(), commit=AsyncMock(), refresh=AsyncMock(),
        )
        portal_settings = SimpleNamespace(
            PAYMENT_PORTAL_RETURN_URL="https://event.example.test/result",
            PAYMENT_PORTAL_SERVICE_CODE="HARI_SANTRI_2026",
        )
        async def create_remote(_payload, key):
            self.assertEqual(0, db.commit.await_count, "transaction/row lock must remain held during remote create")
            self.assertEqual(f"hari-santri-order-{order_id}", key)
            return {"payment_id": "portal-id-1", "reference_id": order.order_number, "amount": 125000, "currency": "IDR", "status": "PENDING", "payment_url": "https://portal.example.test/pay"}

        with patch("app.modules.hari_santri.service.PaymentPortalClient._settings", return_value=portal_settings), patch(
            "app.modules.hari_santri.service.PaymentPortalClient.create_payment", side_effect=create_remote
        ) as remote_create:
            result = await HariSantriService.create_checkout(db, order_id, user_id)

        self.assertEqual("portal-id-1", result["payment_id"])
        self.assertEqual(1, remote_create.await_count)
        self.assertIn("FOR UPDATE", str(db.execute.await_args_list[0].args[0]).upper())
        local_attempt = next(call.args[0] for call in db.add.call_args_list if isinstance(call.args[0], HariSantriPayment))
        self.assertEqual("portal-id-1", local_attempt.payment_portal_id)
        db.commit.assert_awaited_once()

    async def test_checkout_timeout_persists_unknown_attempt_for_same_key_retry(self):
        from decimal import Decimal

        from app.modules.hari_santri.models import HariSantriPayment

        order_id, user_id, event_id = uuid4(), uuid4(), uuid4()
        order = SimpleNamespace(
            id=order_id, user_id=user_id, event_id=event_id, order_number="HS-TIMEOUT-1",
            status="pending", total_amount=Decimal("125000"), currency="IDR", expires_at=None,
        )

        class Result:
            def __init__(self, value=None, rows=None):
                self.value = value
                self.rows = rows or []

            def scalar_one_or_none(self):
                return self.value

            def scalars(self):
                return self

            def all(self):
                return self.rows

        db = SimpleNamespace(
            execute=AsyncMock(side_effect=[
                Result(value=order), Result(value=uuid4()),
                Result(rows=[SimpleNamespace(product_code="FAMILY_WALK")]), Result(value=None),
            ]),
            get=AsyncMock(side_effect=[
                SimpleNamespace(id=event_id, slug="hari-santri-2026", name="Hari Santri 2026"),
                SimpleNamespace(email="person@example.test", full_name="Test User"),
            ]),
            add=Mock(), flush=AsyncMock(), commit=AsyncMock(), refresh=AsyncMock(),
        )
        with patch("app.modules.hari_santri.service.PaymentPortalClient._settings", return_value=SimpleNamespace(
            PAYMENT_PORTAL_RETURN_URL="https://event.example.test/result",
            PAYMENT_PORTAL_SERVICE_CODE="HARI_SANTRI_2026",
        )), patch(
            "app.modules.hari_santri.service.PaymentPortalClient.create_payment",
            new_callable=AsyncMock,
            side_effect=AppException("PAYMENT_PORTAL_UNAVAILABLE", "timeout"),
        ) as create_remote:
            with self.assertRaises(AppException):
                await HariSantriService.create_checkout(db, order_id, user_id)

        local_attempt = next(call.args[0] for call in db.add.call_args_list if isinstance(call.args[0], HariSantriPayment))
        self.assertEqual("unknown", local_attempt.status)
        self.assertIsNone(local_attempt.payment_portal_id)
        self.assertEqual(f"hari-santri-order-{order_id}", create_remote.await_args.args[1])
        db.commit.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
