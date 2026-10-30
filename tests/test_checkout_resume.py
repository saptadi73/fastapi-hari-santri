import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

from app.modules.hari_santri.service import HariSantriService


class CheckoutResumeTests(unittest.IsolatedAsyncioTestCase):
    async def test_existing_payment_renews_session_without_creating_payment_or_order(self):
        user_id, event_id, order_id = uuid4(), uuid4(), uuid4()
        order = SimpleNamespace(id=order_id, user_id=user_id, event_id=event_id,
                                order_number="RESUME", status="pending", total_amount=25000,
                                currency="IDR", expires_at=None)
        payment = SimpleNamespace(payment_portal_id="same-payment", payment_no="PAY-SAME",
                                  reference_id="RESUME", payment_url="https://portal.test/old",
                                  status="PENDING", expires_at=None, currency="IDR", amount=25000)
        def result(value):
            return SimpleNamespace(scalar_one_or_none=lambda: value,
                                   scalars=lambda: SimpleNamespace(all=lambda: value))
        db = SimpleNamespace(execute=AsyncMock(side_effect=[result(order), result(uuid4()),
                             result([SimpleNamespace(product_code="WALK")]), result(payment)]),
                             get=AsyncMock(side_effect=[SimpleNamespace(id=event_id, slug="harlah-nu-101", name="Harlah NU"),
                                                       SimpleNamespace(email="test@example.test", full_name="Test")]),
                             add=Mock(), commit=AsyncMock(), refresh=AsyncMock())
        with patch("app.modules.hari_santri.service.PaymentPortalClient._settings", return_value=SimpleNamespace(
            PAYMENT_PORTAL_RETURN_URL="https://event.test/result", PAYMENT_PORTAL_SERVICE_CODE="TiketSantri2026"
        )), patch("app.modules.hari_santri.service.PaymentPortalClient.create_payment", new_callable=AsyncMock) as create, patch(
            "app.modules.hari_santri.service.PaymentPortalClient.renew_checkout", new_callable=AsyncMock,
            return_value={"payment_url": "https://portal.test/new", "status": "PENDING"}
        ) as renew:
            response = await HariSantriService.create_checkout(db, order_id, user_id)
        renew.assert_awaited_once_with("same-payment")
        create.assert_not_awaited()
        db.add.assert_not_called()
        self.assertEqual("same-payment", response["payment_id"])
        self.assertEqual("https://portal.test/new", response["payment_url"])
