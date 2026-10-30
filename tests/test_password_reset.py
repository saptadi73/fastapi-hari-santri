import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from app.core.email import frontend_login_url, password_reset_url
from app.core.exceptions import ValidationException
from app.modules.users.models import PasswordResetToken
from app.modules.users.service import UserService


class PasswordResetUrlTests(unittest.TestCase):
    def test_frontend_reset_url_contains_encoded_token(self):
        self.assertIn("token=abc%2B%2F%3D", password_reset_url("abc+/="))

    def test_email_routes_fall_back_to_frontend_url(self):
        settings = SimpleNamespace(
            FRONTEND_URL="https://harlahnu.com/",
            FRONTEND_LOGIN_URL="",
            FRONTEND_RESET_PASSWORD_URL="",
        )
        with patch("app.core.email.get_settings", return_value=settings):
            self.assertEqual("https://harlahnu.com/auth/login", frontend_login_url())
            self.assertEqual(
                "https://harlahnu.com/auth/reset-password?token=token",
                password_reset_url("token"),
            )

    def test_email_routes_use_primary_origin_from_cors_allow_list(self):
        settings = SimpleNamespace(
            FRONTEND_URL="https://harlahnu.com,https://harisantri2026.com",
            FRONTEND_LOGIN_URL="",
            FRONTEND_RESET_PASSWORD_URL="",
        )
        with patch("app.core.email.get_settings", return_value=settings):
            self.assertEqual("https://harlahnu.com/auth/login", frontend_login_url())
            self.assertEqual(
                "https://harlahnu.com/auth/reset-password?token=token",
                password_reset_url("token"),
            )


class PasswordResetServiceTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def db():
        db = MagicMock()
        db.execute = AsyncMock()
        db.commit = AsyncMock()
        return db

    async def test_forgot_password_stores_hash_and_returns_delivery_token(self):
        db = self.db()
        user = SimpleNamespace(id=uuid4(), email="person@example.com")
        with patch("app.modules.users.service.UserRepository.get_by_email", AsyncMock(return_value=user)):
            email, token = await UserService.forgot_password(db, user.email)

        self.assertEqual(user.email, email)
        stored = db.add.call_args.args[0]
        self.assertIsInstance(stored, PasswordResetToken)
        self.assertNotEqual(token, stored.token_hash)
        self.assertEqual(64, len(stored.token_hash))
        db.commit.assert_awaited_once()

    async def test_unknown_email_has_no_delivery(self):
        db = self.db()
        with patch("app.modules.users.service.UserRepository.get_by_email", AsyncMock(return_value=None)):
            self.assertIsNone(await UserService.forgot_password(db, "unknown@example.com"))
        db.add.assert_not_called()
        db.commit.assert_not_awaited()

    async def test_reset_consumes_token_and_changes_password_atomically(self):
        db = self.db()
        user = SimpleNamespace(id=uuid4(), password_hash="old")
        token_row = SimpleNamespace(
            user_id=user.id,
            used_at=None,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
        )
        result = MagicMock()
        result.scalar_one_or_none.return_value = token_row
        db.execute.side_effect = [result, MagicMock()]
        with (
            patch("app.modules.users.service.UserRepository.get_by_id", AsyncMock(return_value=user)),
            patch("app.modules.users.service.hash_password", return_value="new-hash"),
        ):
            changed = await UserService.reset_password(
                db, "plain-token", "NewPassword!2", "NewPassword!2"
            )

        self.assertTrue(changed)
        self.assertEqual("new-hash", user.password_hash)
        self.assertIsNotNone(token_row.used_at)
        db.commit.assert_awaited_once()

    async def test_used_token_is_rejected(self):
        db = self.db()
        result = MagicMock()
        result.scalar_one_or_none.return_value = SimpleNamespace(used_at=datetime.now(timezone.utc))
        db.execute.return_value = result
        with self.assertRaises(ValidationException) as caught:
            await UserService.reset_password(db, "plain-token", "NewPassword!2", "NewPassword!2")
        self.assertEqual("INVALID_TOKEN", caught.exception.code)

    async def test_expired_token_is_rejected(self):
        db = self.db()
        result = MagicMock()
        result.scalar_one_or_none.return_value = SimpleNamespace(
            used_at=None,
            expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        )
        db.execute.return_value = result
        with self.assertRaises(ValidationException) as caught:
            await UserService.reset_password(db, "plain-token", "NewPassword!2", "NewPassword!2")
        self.assertEqual("EXPIRED_TOKEN", caught.exception.code)


if __name__ == "__main__":
    unittest.main()
