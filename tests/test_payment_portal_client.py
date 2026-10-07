import unittest
import base64
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from app.core.exceptions import AppException
from app.modules.hari_santri.payment_portal import PaymentPortalClient


def portal_settings():
    return SimpleNamespace(
        PAYMENT_PORTAL_BASE_URL="https://portal.example.test",
        PAYMENT_PORTAL_CLIENT_ID="client-test",
        PAYMENT_PORTAL_CLIENT_SECRET="client-secret-test",
        PAYMENT_PORTAL_CALLBACK_SECRET="callback-secret-test",
        PAYMENT_PORTAL_RETURN_URL="https://event.example.test/result",
        PAYMENT_PORTAL_SERVICE_CODE="HARI_SANTRI_2026",
        PAYMENT_PORTAL_TIMEOUT_SECONDS=2,
        PAYMENT_PORTAL_CALLBACK_TOLERANCE_SECONDS=300,
        APP_ENV="test",
        PUBLIC_BASE_URL="https://api.example.test",
    )


class PaymentPortalClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_oauth_create_and_idempotent_replay_use_same_key(self):
        seen = []

        def handler(request):
            seen.append(request)
            if request.url.path.endswith("/oauth/token"):
                return httpx.Response(200, json={"access_token": "token-test"})
            return httpx.Response(200, json={"data": {
                "payment_id": "portal-payment-1", "reference_id": "HS-1", "amount": 125000,
                "currency": "IDR", "service_code": "HARI_SANTRI_2026", "status": "PENDING",
                "payment_url": "https://portal.example.test/checkout/1",
            }})

        original_client = httpx.AsyncClient

        def client_factory(**kwargs):
            return original_client(transport=httpx.MockTransport(handler), timeout=kwargs["timeout"])

        with patch("app.modules.hari_santri.payment_portal.get_settings", return_value=portal_settings()), patch(
            "app.modules.hari_santri.payment_portal.httpx.AsyncClient", side_effect=client_factory
        ):
            first = await PaymentPortalClient.create_payment({"reference_id": "HS-1"}, "hs-order-1")
            second = await PaymentPortalClient.create_payment({"reference_id": "HS-1"}, "hs-order-1")

        self.assertEqual(first["payment_id"], second["payment_id"])
        token_requests = [request for request in seen if request.url.path.endswith("/oauth/token")]
        payment_requests = [request for request in seen if request.url.path.endswith("/client/payments")]
        self.assertEqual(2, len(token_requests))
        basic_value = token_requests[0].headers["Authorization"].removeprefix("Basic ")
        self.assertEqual("client-test:client-secret-test", base64.b64decode(basic_value).decode())
        self.assertIn("scope=payments%3Aread+payments%3Awrite", token_requests[0].content.decode())
        self.assertEqual(["hs-order-1", "hs-order-1"], [request.headers["Idempotency-Key"] for request in payment_requests])
        self.assertEqual("Bearer token-test", payment_requests[0].headers["Authorization"])

    async def test_server_payment_lookup_uses_oauth_read_scope_and_payment_id(self):
        seen = []

        def handler(request):
            seen.append(request)
            if request.url.path.endswith("/oauth/token"):
                return httpx.Response(200, json={"access_token": "token-test"})
            return httpx.Response(200, json={"data": {"payment_id": "p/1", "status": "PAID"}})

        original_client = httpx.AsyncClient
        with patch("app.modules.hari_santri.payment_portal.get_settings", return_value=portal_settings()), patch(
            "app.modules.hari_santri.payment_portal.httpx.AsyncClient",
            side_effect=lambda **kwargs: original_client(transport=httpx.MockTransport(handler), timeout=kwargs["timeout"]),
        ):
            result = await PaymentPortalClient.get_payment("p/1")

        lookup = seen[-1]
        self.assertEqual("PAID", result["status"])
        self.assertEqual("GET", lookup.method)
        self.assertEqual(b"/api/v1/client/payments/p%2F1", lookup.url.raw_path)
        self.assertEqual("Bearer token-test", lookup.headers["Authorization"])

    async def test_create_timeout_is_reported_as_unknown_portal_availability(self):
        def handler(request):
            if request.url.path.endswith("/oauth/token"):
                return httpx.Response(200, json={"access_token": "token-test"})
            raise httpx.ReadTimeout("simulated timeout", request=request)

        original_client = httpx.AsyncClient
        with patch("app.modules.hari_santri.payment_portal.get_settings", return_value=portal_settings()), patch(
            "app.modules.hari_santri.payment_portal.httpx.AsyncClient",
            side_effect=lambda **kwargs: original_client(transport=httpx.MockTransport(handler), timeout=kwargs["timeout"]),
        ):
            with self.assertRaises(AppException) as caught:
                await PaymentPortalClient.create_payment({"reference_id": "HS-1"}, "hs-order-1")

        self.assertEqual("PAYMENT_PORTAL_UNAVAILABLE", caught.exception.code)


if __name__ == "__main__":
    unittest.main()
