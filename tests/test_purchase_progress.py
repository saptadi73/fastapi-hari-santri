import unittest
from app.modules.users.purchase_progress import purchase_status


class PurchaseProgressTests(unittest.TestCase):
    def product(self, status, payment_status="pending", **extra):
        return {"source": "order", "order_status": status, "payment_status": payment_status, **extra}

    def test_expired_first_attempt_keeps_payment_pending(self):
        self.assertEqual("payment_pending", purchase_status([self.product("pending", "expired")], registered=False, complete=False))

    def test_first_success_is_not_full_settlement(self):
        self.assertEqual("payment_pending", purchase_status([self.product("partially_paid", "success")], registered=False, complete=False))

    def test_complete_form_does_not_unlock_partial_order(self):
        self.assertEqual("payment_pending", purchase_status([self.product("partially_paid", "success")], registered=True, complete=True))

    def test_complete_form_does_not_unlock_unpaid_order(self):
        self.assertEqual("payment_pending", purchase_status([self.product("pending")], registered=True, complete=True))

    def test_paid_order_requires_profile(self):
        self.assertEqual("paid_profile_incomplete", purchase_status([self.product("paid", "success")], registered=False, complete=False))

    def test_paid_order_and_complete_profile_unlock_registration(self):
        self.assertEqual("completed", purchase_status([self.product("paid", "success")], registered=True, complete=True))

    def test_explicit_incomplete_aggregate_overrides_paid_label(self):
        self.assertEqual("payment_pending", purchase_status([self.product("paid", "success", is_payment_complete=False)], registered=True, complete=True))

    def test_older_paid_order_does_not_hide_new_pending_order(self):
        self.assertEqual("payment_pending", purchase_status([self.product("paid", "success"), self.product("pending")], registered=True, complete=True))

    def test_unselected_and_cart_states(self):
        self.assertEqual("not_selected", purchase_status([], registered=False, complete=False))
        self.assertEqual("selected", purchase_status([{"source": "cart"}], registered=False, complete=False))


class RegistrationSnapshotTests(unittest.IsolatedAsyncioTestCase):
    async def test_snapshot_exposes_partial_balance_without_unlocking_profile(self):
        from datetime import datetime, timezone
        from decimal import Decimal
        from types import SimpleNamespace
        from uuid import uuid4
        from unittest.mock import AsyncMock, MagicMock, patch
        from app.main import app
        from app.modules.users.service import UserService
        from app.modules.users.repository import UserRepository
        from app.modules.payments.service import PaymentService

        now = datetime.now(timezone.utc)
        user = SimpleNamespace(id=uuid4(), email="participant@example.test", full_name="Participant", phone=None, country=None, preferred_locale="id", status="active", registration_status="account_created", role="participant", is_email_verified=True, created_at=now)
        order = SimpleNamespace(id=uuid4(), order_number="ORD-PARTIAL", registration_id=None, event_id=uuid4(), allowed_actions=["continue_payment"], status="partially_paid", subtotal=Decimal("12600000"), total_amount=Decimal("12600000"), currency="IDR", created_at=now)
        item = SimpleNamespace(product_id=uuid4(), product_code="DELEGATE_A", product_name="Delegate A", product_type="delegate", quantity=1, unit_price=Decimal("12600000"), line_total=Decimal("12600000"), currency="IDR")
        payment = SimpleNamespace(id=uuid4(), transaction_status="success", provider="midtrans", paid_at=now)
        results = [MagicMock() for _ in range(5)]
        results[0].scalar_one_or_none.return_value = None
        results[1].scalars.return_value.all.return_value = [order]
        results[2].scalars.return_value.all.return_value = [item]
        results[3].scalars.return_value.first.return_value = payment
        results[4].all.return_value = []
        db = AsyncMock()
        db.execute.side_effect = results
        with patch.object(UserRepository, "get_by_id", AsyncMock(return_value=user)), patch.object(PaymentService, "_payment_progress", AsyncMock(return_value=(Decimal("9000000"), Decimal("3600000")))):
            result = await UserService.get_registration_detail(db, user.id)
        self.assertEqual("payment_pending", result["registration_status"])
        self.assertEqual("payment_pending", result["purchase_tracking"]["delegate"]["status"])
        self.assertFalse(result["purchase_tracking"]["delegate"]["profile_required"])
        self.assertEqual(3600000, result["orders"][0]["remaining_amount"])
        self.assertFalse(result["orders"][0]["is_payment_complete"])
        db.commit.assert_not_awaited()

    async def test_payment_read_preserves_checkout_expiry(self):
        from datetime import datetime, timezone
        from uuid import uuid4
        from app.modules.payments.schemas import PaymentRead
        expiry = datetime.now(timezone.utc)
        payment = PaymentRead(id=uuid4(), order_id=uuid4(), provider="midtrans", gross_amount=9000000, currency="IDR", transaction_status="pending", expired_at=expiry)
        self.assertEqual(expiry, payment.expired_at)
