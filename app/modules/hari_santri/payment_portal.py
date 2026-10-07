import base64
import hashlib
import hmac
import json
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote, urlparse

import httpx

from app.core.config import get_settings
from app.core.exceptions import AppException


class PaymentPortalClient:
    @staticmethod
    def _settings():
        settings = get_settings()
        required = (
            settings.PAYMENT_PORTAL_BASE_URL,
            settings.PAYMENT_PORTAL_CLIENT_ID,
            settings.PAYMENT_PORTAL_CLIENT_SECRET,
            settings.PAYMENT_PORTAL_CALLBACK_SECRET,
            settings.PAYMENT_PORTAL_RETURN_URL,
        )
        if not all(required):
            raise AppException("PAYMENT_PORTAL_NOT_CONFIGURED", "Integrasi Payment Portal belum dikonfigurasi")
        if settings.PAYMENT_PORTAL_SERVICE_CODE != "HARI_SANTRI_2026":
            raise AppException("PAYMENT_PORTAL_NOT_CONFIGURED", "Service code Payment Portal tidak sesuai")
        if settings.APP_ENV.lower() in {"prod", "production"}:
            production_urls = (
                settings.PAYMENT_PORTAL_BASE_URL,
                settings.PAYMENT_PORTAL_RETURN_URL,
                settings.PUBLIC_BASE_URL,
            )
            if any(urlparse(value).scheme != "https" or not urlparse(value).netloc for value in production_urls):
                raise AppException("PAYMENT_PORTAL_NOT_CONFIGURED", "URL Payment Portal dan callback/return di produksi wajib menggunakan HTTPS")
        return settings

    @staticmethod
    async def _access_token(client: httpx.AsyncClient, settings) -> str:
        response = await client.post(
            f"{settings.PAYMENT_PORTAL_BASE_URL.rstrip('/')}/api/v1/oauth/token",
            auth=(settings.PAYMENT_PORTAL_CLIENT_ID, settings.PAYMENT_PORTAL_CLIENT_SECRET),
            data={"grant_type": "client_credentials", "scope": "payments:read payments:write"},
        )
        response.raise_for_status()
        token = response.json().get("access_token")
        if not isinstance(token, str) or not token:
            raise AppException("PAYMENT_PORTAL_INVALID_RESPONSE", "Payment Portal tidak memberikan access token")
        return token

    @staticmethod
    async def create_payment(payload: dict[str, Any], idempotency_key: str) -> dict[str, Any]:
        settings = PaymentPortalClient._settings()
        try:
            async with httpx.AsyncClient(timeout=settings.PAYMENT_PORTAL_TIMEOUT_SECONDS) as client:
                token = await PaymentPortalClient._access_token(client, settings)
                response = await client.post(
                    f"{settings.PAYMENT_PORTAL_BASE_URL.rstrip('/')}/api/v1/client/payments",
                    headers={"Authorization": f"Bearer {token}", "Idempotency-Key": idempotency_key},
                    json=payload,
                )
                response.raise_for_status()
                body = response.json()
        except httpx.HTTPError as exc:
            raise AppException("PAYMENT_PORTAL_UNAVAILABLE", "Payment Portal tidak dapat dihubungi") from exc
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, dict):
            raise AppException("PAYMENT_PORTAL_INVALID_RESPONSE", "Format respons Payment Portal tidak valid")
        return data

    @staticmethod
    async def renew_checkout(payment_id: str) -> dict[str, Any]:
        settings = PaymentPortalClient._settings()
        try:
            async with httpx.AsyncClient(timeout=settings.PAYMENT_PORTAL_TIMEOUT_SECONDS) as client:
                token = await PaymentPortalClient._access_token(client, settings)
                response = await client.post(
                    f"{settings.PAYMENT_PORTAL_BASE_URL.rstrip('/')}/api/v1/client/payments/{payment_id}/checkout",
                    headers={"Authorization": f"Bearer {token}"},
                )
                response.raise_for_status()
                body = response.json()
        except httpx.HTTPError as exc:
            raise AppException("PAYMENT_PORTAL_UNAVAILABLE", "Checkout Payment Portal tidak dapat diperbarui") from exc
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, dict):
            raise AppException("PAYMENT_PORTAL_INVALID_RESPONSE", "Format respons checkout tidak valid")
        return data

    @staticmethod
    async def get_payment(payment_id: str) -> dict[str, Any]:
        """Read payment state server-to-server; never expose this as browser authority."""
        settings = PaymentPortalClient._settings()
        try:
            async with httpx.AsyncClient(timeout=settings.PAYMENT_PORTAL_TIMEOUT_SECONDS) as client:
                token = await PaymentPortalClient._access_token(client, settings)
                response = await client.get(
                    f"{settings.PAYMENT_PORTAL_BASE_URL.rstrip('/')}/api/v1/client/payments/{quote(payment_id, safe='')}",
                    headers={"Authorization": f"Bearer {token}"},
                )
                response.raise_for_status()
                body = response.json()
        except httpx.HTTPError as exc:
            raise AppException("PAYMENT_PORTAL_UNAVAILABLE", "Status pembayaran Payment Portal tidak dapat diperiksa") from exc
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, dict):
            raise AppException("PAYMENT_PORTAL_INVALID_RESPONSE", "Format lookup Payment Portal tidak valid")
        return data

    @staticmethod
    def verify_callback(raw_body: bytes, event_id: str, timestamp: str, signature: str) -> dict[str, Any]:
        settings = get_settings()
        if not settings.PAYMENT_PORTAL_CALLBACK_SECRET:
            raise AppException("PAYMENT_PORTAL_NOT_CONFIGURED", "Callback Payment Portal belum dikonfigurasi")
        try:
            occurred = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            if occurred.tzinfo is None:
                raise ValueError("timezone required")
            age_seconds = abs((datetime.now(timezone.utc) - occurred.astimezone(timezone.utc)).total_seconds())
            if age_seconds > settings.PAYMENT_PORTAL_CALLBACK_TOLERANCE_SECONDS:
                raise ValueError("timestamp outside tolerance")
            expected = base64.b64encode(hmac.new(
                settings.PAYMENT_PORTAL_CALLBACK_SECRET.encode(),
                timestamp.encode() + b"." + raw_body,
                hashlib.sha256,
            ).digest()).decode("ascii")
            if not hmac.compare_digest(expected, signature):
                raise ValueError("signature mismatch")
            payload = json.loads(raw_body)
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AppException("INVALID_PAYMENT_PORTAL_CALLBACK", "Signature atau timestamp callback tidak valid") from exc
        if not isinstance(payload, dict) or payload.get("event_id") != event_id:
            raise AppException("INVALID_PAYMENT_PORTAL_CALLBACK", "Identitas event callback tidak valid")
        if not isinstance(payload.get("data"), dict) or not payload.get("event_type"):
            raise AppException("INVALID_PAYMENT_PORTAL_CALLBACK", "Payload callback tidak lengkap")
        return payload
