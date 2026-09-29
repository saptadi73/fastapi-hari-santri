import asyncio
import base64
import hashlib
import hmac
import json
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from pydantic import ValidationError

from app.core.exceptions import AppException, ConflictException, ValidationException
from app.modules.hari_santri.payment_portal import PaymentPortalClient
from app.modules.hari_santri.schemas import OrderParticipantWrite
from app.modules.hari_santri.service import HariSantriService
from app.modules.payments.service import PaymentService
from app.modules.store.service import StoreService


class HariSantriContractTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
