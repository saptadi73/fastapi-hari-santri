import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from app.core.exceptions import ConflictException, NotFoundException, ValidationException
from app.modules.hari_santri.service import HariSantriService


class Result:
    def __init__(self, value=None):
        self.value = value

    def scalar_one_or_none(self):
        return self.value

    def scalar_one(self):
        return self.value


class HariSantriWalletContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_retry_with_same_request_id_returns_existing_transfer(self):
        transfer_id = uuid4()
        participant_id = uuid4()
        exhibitor_id = uuid4()
        prior = SimpleNamespace(id=transfer_id, request_id="retry-1234", participant_id=participant_id, exhibitor_id=exhibitor_id, amount=25000, created_at=datetime.now(timezone.utc))
        participant_wallet = SimpleNamespace(balance=75000)
        exhibitor_wallet = SimpleNamespace(balance=125000)
        db = SimpleNamespace(execute=AsyncMock(side_effect=[Result(), Result(prior), Result(participant_wallet), Result(exhibitor_wallet)]), commit=AsyncMock())

        result = await HariSantriService.transfer_voucher(db, "opaque-qr-token", 25000, "retry-1234", "password", exhibitor_id, uuid4())

        self.assertEqual(transfer_id, result["transfer_id"])
        self.assertEqual(75000, result["participant_balance"])
        self.assertEqual(125000, result["exhibitor_balance"])
        db.commit.assert_not_called()
        self.assertIn("pg_advisory_xact_lock", str(db.execute.call_args_list[0].args[0]))

    async def test_request_id_reuse_with_different_amount_is_rejected(self):
        prior = SimpleNamespace(id=uuid4(), request_id="retry-1234", participant_id=uuid4(), exhibitor_id=uuid4(), amount=25000)
        db = SimpleNamespace(execute=AsyncMock(side_effect=[Result(), Result(prior)]), commit=AsyncMock())

        with self.assertRaises(ConflictException) as caught:
            await HariSantriService.transfer_voucher(db, "opaque-qr-token", 30000, "retry-1234", "password", prior.exhibitor_id, uuid4())

        self.assertEqual("WALLET_REQUEST_REUSED", caught.exception.code)

    async def test_invalid_qr_is_rejected_before_wallet_mutation(self):
        db = SimpleNamespace(execute=AsyncMock(side_effect=[Result(), Result(None), Result(None)]), commit=AsyncMock())

        with self.assertRaises(NotFoundException) as caught:
            await HariSantriService.transfer_voucher(db, "invalid-qr-token", 10000, "invalid-qr-1", "password", uuid4(), uuid4())

        self.assertEqual("VOUCHER_NOT_FOUND", caught.exception.code)
        db.commit.assert_not_called()

    async def test_expired_voucher_is_rejected(self):
        voucher = SimpleNamespace(status="active", expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
        db = SimpleNamespace(execute=AsyncMock(side_effect=[Result(), Result(None), Result(voucher)]), commit=AsyncMock())

        with self.assertRaises(ConflictException) as caught:
            await HariSantriService.transfer_voucher(db, "expired-qr-token", 10000, "expired-qr-1", "password", uuid4(), uuid4())

        self.assertEqual("VOUCHER_EXPIRED", caught.exception.code)

    async def test_exhibitor_owner_is_required_for_charge(self):
        voucher = SimpleNamespace(status="active", expires_at=datetime.now(timezone.utc) + timedelta(hours=1), participant_id=uuid4(), wallet_id=uuid4())
        participant = SimpleNamespace(id=voucher.participant_id, order_id=uuid4())
        exhibitor = SimpleNamespace(id=uuid4(), user_id=uuid4(), status="submitted")
        db = SimpleNamespace(
            execute=AsyncMock(side_effect=[Result(), Result(None), Result(voucher), Result(participant), Result(exhibitor)]),
            commit=AsyncMock(),
        )

        with self.assertRaises(ValidationException) as caught:
            await HariSantriService.transfer_voucher(db, "valid-qr-token", 10000, "owner-check-1", "password", exhibitor.id, uuid4())

        self.assertEqual("EXHIBITOR_ACCESS_DENIED", caught.exception.code)

    async def test_participant_password_is_required(self):
        participant_user_id = uuid4()
        voucher = SimpleNamespace(status="active", expires_at=datetime.now(timezone.utc) + timedelta(hours=1), participant_id=uuid4(), wallet_id=uuid4())
        participant = SimpleNamespace(id=voucher.participant_id, order_id=uuid4())
        exhibitor = SimpleNamespace(id=uuid4(), user_id=uuid4(), status="submitted")
        order = SimpleNamespace(user_id=participant_user_id)
        account = SimpleNamespace(password_hash="hashed-password")
        db = SimpleNamespace(
            execute=AsyncMock(side_effect=[Result(), Result(None), Result(voucher), Result(participant), Result(exhibitor)]),
            get=AsyncMock(side_effect=[order, account]),
            commit=AsyncMock(),
        )

        with patch("app.modules.hari_santri.service.verify_password", return_value=False):
            with self.assertRaises(ValidationException) as caught:
                await HariSantriService.transfer_voucher(db, "valid-qr-token", 10000, "password-check-1", "wrong", exhibitor.id, exhibitor.user_id)

        self.assertEqual("PARTICIPANT_CONFIRMATION_FAILED", caught.exception.code)
        db.commit.assert_not_called()

    async def test_insufficient_balance_is_rejected(self):
        participant_user_id = uuid4()
        voucher = SimpleNamespace(status="active", expires_at=datetime.now(timezone.utc) + timedelta(hours=1), participant_id=uuid4(), wallet_id=uuid4())
        participant = SimpleNamespace(id=voucher.participant_id, order_id=uuid4())
        exhibitor = SimpleNamespace(id=uuid4(), user_id=uuid4(), status="submitted")
        order = SimpleNamespace(user_id=participant_user_id)
        account = SimpleNamespace(password_hash="hashed-password")
        participant_wallet = SimpleNamespace(balance=5000)
        exhibitor_wallet = SimpleNamespace(balance=1000)
        db = SimpleNamespace(
            execute=AsyncMock(side_effect=[Result(), Result(None), Result(voucher), Result(participant), Result(exhibitor), Result(participant_wallet), Result(exhibitor_wallet)]),
            get=AsyncMock(side_effect=[order, account]),
            commit=AsyncMock(),
        )

        with patch("app.modules.hari_santri.service.verify_password", return_value=True):
            with self.assertRaises(ConflictException) as caught:
                await HariSantriService.transfer_voucher(db, "valid-qr-token", 10000, "balance-check-1", "correct", exhibitor.id, exhibitor.user_id)

        self.assertEqual("INSUFFICIENT_VOUCHER_BALANCE", caught.exception.code)
        db.commit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
