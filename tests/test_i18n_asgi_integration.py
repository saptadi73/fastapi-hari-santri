import json
import unittest

from app.main import app


async def asgi_get(path: str, *, query: str = "", headers: list[tuple[bytes, bytes]] | None = None):
    sent = []
    request_sent = False

    async def receive():
        nonlocal request_sent
        if not request_sent:
            request_sent = True
            return {"type": "http.request", "body": b"", "more_body": False}
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": query.encode(),
        "headers": headers or [],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
        "root_path": "",
    }
    await app(scope, receive, send)
    start = next(message for message in sent if message["type"] == "http.response.start")
    body = b"".join(message.get("body", b"") for message in sent if message["type"] == "http.response.body")
    response_headers = {key.decode().lower(): value.decode() for key, value in start["headers"]}
    return start["status"], response_headers, json.loads(body)


class LocaleAsgiIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_query_indonesian_localizes_real_endpoint_and_preserves_machine_data(self):
        status, headers, payload = await asgi_get("/api/v1/health", query="locale=id")
        self.assertEqual(200, status)
        self.assertEqual("id", headers["content-language"])
        self.assertEqual("Berhasil", payload["message"])
        self.assertEqual({"status": "alive"}, payload["data"])
        self.assertTrue(payload["success"])

    async def test_accept_language_selects_indonesian_on_real_endpoint(self):
        status, headers, payload = await asgi_get(
            "/api/v1/health",
            headers=[(b"accept-language", b"id-ID,id;q=0.9,en;q=0.8")],
        )
        self.assertEqual(200, status)
        self.assertEqual("id", headers["content-language"])
        self.assertEqual("alive", payload["data"]["status"])

    async def test_legacy_chinese_header_falls_back_to_indonesian(self):
        _, headers, _ = await asgi_get("/api/v1/health", headers=[(b"accept-language", b"zh-CN")])
        self.assertEqual("id", headers["content-language"])

    async def test_query_locale_has_priority_over_header(self):
        _, headers, payload = await asgi_get(
            "/api/v1/health",
            query="locale=en",
            headers=[(b"accept-language", b"id")],
        )
        self.assertEqual("en", headers["content-language"])
        self.assertEqual("OK", payload["message"])


if __name__ == "__main__":
    unittest.main()
