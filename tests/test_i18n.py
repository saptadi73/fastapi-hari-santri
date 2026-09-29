import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from fastapi.exceptions import RequestValidationError
from starlette.requests import Request
from starlette.responses import Response

from app.core.i18n import DEFAULT_LOCALE, SUPPORTED_LOCALES, normalize_locale, request_locale, translate_error_message, translate_message
from app.support.responses import fail_response, success_response
from app.main import app
from app.middleware.locale import LocaleMiddleware
from app.modules.email_notifications.service import DEFAULT_TEMPLATES_BY_LOCALE, TRIGGER_VARIABLES
from app.modules.payments import routes as payment_routes
from app.modules.users.schemas import UserCreate, UserUpdate


class _Request:
    def __init__(self, query=None, headers=None):
        self.query_params = query or {}
        self.headers = headers or {}
        self.state = SimpleNamespace(request_id="req-i18n")


class I18nTest(unittest.TestCase):
    def test_supported_locale_normalization(self):
        self.assertEqual("id", DEFAULT_LOCALE)
        self.assertEqual(("id", "en"), SUPPORTED_LOCALES)
        self.assertEqual("id", normalize_locale("id-ID"))
        self.assertEqual("en", normalize_locale("en-US"))
        self.assertEqual("id", normalize_locale("zh-Hans-CN"))
        self.assertEqual("id", normalize_locale("zh_cn"))

    def test_query_locale_wins_and_chinese_header_falls_back_to_indonesian(self):
        request = _Request({"locale": "en"}, {"accept-language": "id-ID,id;q=0.9"})
        self.assertEqual("en", request_locale(request))
        self.assertEqual("id", request_locale(_Request(headers={"accept-language": "zh-CN,zh;q=0.9"})))

    def test_indonesian_and_english_response_messages(self):
        indonesian = success_response("Login berhasil", request=_Request(headers={"accept-language": "id"}))
        english = success_response("Login berhasil", request=_Request(headers={"accept-language": "en"}))
        self.assertEqual("Login berhasil", indonesian["message"])
        self.assertEqual("Signed in successfully", english["message"])
        self.assertEqual("Pesan module baru", success_response("Pesan module baru", request=_Request(headers={"accept-language": "id"}))["message"])

        failure = fail_response("Validation failed", [{"field": "name", "code": "FORBIDDEN", "message": "Organizer role required"}], _Request(headers={"accept-language": "id"}))
        self.assertEqual("Anda tidak memiliki izin untuk melakukan tindakan ini", failure["message"])
        self.assertEqual("Anda tidak memiliki izin untuk melakukan tindakan ini", failure["errors"][0]["message"])

    def test_error_codes_and_validation_messages_are_localized(self):
        self.assertEqual("Pesanan tidak ditemukan", translate_error_message("ORDER_NOT_FOUND", "Any source message", "id"))
        self.assertEqual("Order not found", translate_error_message("ORDER_NOT_FOUND", "Any source message", "en"))
        self.assertEqual("Kolom ini wajib diisi", translate_message("Field required", "id"))
        self.assertEqual("Teks minimal harus berisi 8 karakter", translate_message("String should have at least 8 characters", "id"))

    def test_user_locale_contract(self):
        created = UserCreate(email="hello@example.com", password="password1", country="Indonesia", phone="0812345678")
        self.assertEqual("id", created.preferred_locale)
        self.assertEqual("en", UserUpdate(preferred_locale="en").preferred_locale)
        with self.assertRaises(ValueError):
            UserUpdate(preferred_locale="zh-CN")

    def test_every_email_trigger_has_indonesian_and_english_templates(self):
        self.assertEqual({"id", "en"}, set(DEFAULT_TEMPLATES_BY_LOCALE))
        for templates in DEFAULT_TEMPLATES_BY_LOCALE.values():
            self.assertEqual(set(TRIGGER_VARIABLES), set(templates))
        self.assertIn("Assalamu’alaikum", DEFAULT_TEMPLATES_BY_LOCALE["id"]["account_registered"][1])

    def test_openapi_documents_indonesian_and_english(self):
        parameter = app.openapi()["components"]["parameters"]["LocaleQuery"]
        self.assertEqual(["id", "en"], parameter["schema"]["enum"])


class LocaleMiddlewareTest(unittest.IsolatedAsyncioTestCase):
    async def test_http_response_declares_fallback_indonesian_language(self):
        request = Request({
            "type": "http", "method": "GET", "path": "/", "query_string": b"",
            "headers": [(b"accept-language", b"zh-CN")],
        })

        async def call_next(_request):
            return Response("ok")

        middleware = LocaleMiddleware(lambda scope, receive, send: None)
        with patch("app.middleware.locale.logger.info") as log_info:
            response = await middleware.dispatch(request, call_next)
        self.assertEqual("id", response.headers["content-language"])
        self.assertIn("Accept-Language", response.headers["vary"])
        self.assertEqual("id", log_info.call_args.kwargs["extra"]["locale"])

    async def test_http_and_validation_errors_use_indonesian(self):
        request = Request({"type": "http", "method": "GET", "path": "/", "query_string": b"", "headers": [(b"accept-language", b"id-ID")]})
        response = await app.exception_handlers[HTTPException](request, HTTPException(403, "Organizer role required"))
        payload = json.loads(response.body)
        self.assertEqual("Anda tidak memiliki izin untuk melakukan tindakan ini", payload["message"])

        invalid = Request({"type": "http", "method": "POST", "path": "/", "query_string": b"", "headers": [(b"accept-language", b"id")]})
        exc = RequestValidationError([{"type": "missing", "loc": ("body", "name"), "msg": "Field required", "input": {}}])
        validation_response = await app.exception_handlers[RequestValidationError](invalid, exc)
        validation_payload = json.loads(validation_response.body)
        self.assertEqual("Kolom ini wajib diisi", validation_payload["errors"][0]["message"])

    async def test_payment_webhook_machine_data_is_locale_invariant(self):
        async def request_for(locale):
            consumed = False

            async def receive():
                nonlocal consumed
                if not consumed:
                    consumed = True
                    return {"type": "http.request", "body": b'{"transaction":"paid"}', "more_body": False}
                return {"type": "http.disconnect"}

            return Request({
                "type": "http", "method": "POST", "path": "/webhooks/doku", "query_string": b"",
                "headers": [(b"accept-language", locale.encode())],
            }, receive)

        with patch.object(payment_routes.PaymentService, "handle_doku_notification", AsyncMock(return_value="success")):
            indonesian = await payment_routes.doku_notification(await request_for("id"), db=object())
            english = await payment_routes.doku_notification(await request_for("en"), db=object())

        self.assertEqual(indonesian["data"], english["data"])
        self.assertEqual({"result": "success"}, indonesian["data"])
        self.assertEqual("Notifikasi DOKU berhasil diproses", indonesian["message"])
        self.assertEqual("DOKU notification processed", english["message"])


if __name__ == "__main__":
    unittest.main()
