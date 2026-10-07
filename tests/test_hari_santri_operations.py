import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

from fastapi import HTTPException

from app.core.dependencies import require_checkin_staff
from app.core.exceptions import ConflictException, NotFoundException
from app.modules.hari_santri.models import HariSantriAuditLog, HariSantriOutboxEvent
from app.modules.hari_santri.service import HariSantriService
from app.modules.payments.models import OrderStatus


class FakeResult:
    def __init__(self, rows=None, value=None):
        self.rows = rows or []
        self.value = value

    def scalars(self):
        return self

    def all(self):
        return self.rows

    def scalar_one_or_none(self):
        return self.value

    def scalar_one(self):
        return self.value


class HariSantriOperationsTests(unittest.IsolatedAsyncioTestCase):
    async def test_callback_event_replay_with_same_hash_is_idempotent(self):
        event = SimpleNamespace(payload_hash="c" * 64)
        db = SimpleNamespace(execute=AsyncMock(side_effect=[FakeResult(), FakeResult(value=event)]), commit=AsyncMock())
        result = await HariSantriService.process_verified_payment_callback(
            db, {"event_id": "evt"}, "evt", "c" * 64, "replay-request"
        )
        self.assertEqual({"duplicate": True}, result)
        db.commit.assert_not_awaited()

    async def test_callback_event_replay_with_different_payload_is_rejected(self):
        event = SimpleNamespace(payload_hash="d" * 64)
        db = SimpleNamespace(execute=AsyncMock(side_effect=[FakeResult(), FakeResult(value=event)]), commit=AsyncMock())
        with self.assertRaises(ConflictException) as caught:
            await HariSantriService.process_verified_payment_callback(
                db, {"event_id": "evt"}, "evt", "e" * 64, "replay-request"
            )
        self.assertEqual("PAYMENT_CALLBACK_EVENT_CONFLICT", caught.exception.code)

    async def test_checkin_staff_can_use_limited_dependency(self):
        staff = SimpleNamespace(role="checkin_staff", status="active")
        self.assertIs(staff, await require_checkin_staff(staff))

    async def test_participant_cannot_use_checkin_dependency(self):
        participant = SimpleNamespace(role="participant", status="active")
        with self.assertRaises(HTTPException) as caught:
            await require_checkin_staff(participant)
        self.assertEqual(403, caught.exception.status_code)

    async def test_expiry_releases_inventory_once_and_audits_request_id(self):
        size_id = uuid4()
        order = SimpleNamespace(id=uuid4(), event_id=uuid4(), status=OrderStatus.PENDING, expires_at=datetime.now(timezone.utc) - timedelta(minutes=1))
        participant = SimpleNamespace(status="reserved", shirt_size_id=size_id)
        inventory = SimpleNamespace(size_id=size_id, reserved=2)
        db = SimpleNamespace(
            execute=AsyncMock(side_effect=[FakeResult([order]), FakeResult([participant]), FakeResult([inventory]), FakeResult(value=None)]),
            add=Mock(),
            commit=AsyncMock(),
        )

        result = await HariSantriService.expire_due_hari_santri_orders(db, request_id="worker-run-1")

        self.assertEqual(1, result["expired_orders"])
        self.assertEqual([], result["inventory_mismatch_orders"])
        self.assertEqual(1, inventory.reserved)
        self.assertEqual("expired", participant.status)
        self.assertEqual(OrderStatus.EXPIRED, order.status)
        audit = next(call.args[0] for call in db.add.call_args_list if isinstance(call.args[0], HariSantriAuditLog))
        self.assertEqual("order_reservation_expired", audit.action)
        self.assertEqual("worker-run-1", audit.payload["request_id"])

    async def test_inventory_mismatch_never_decrements_stock_below_zero(self):
        size_id = uuid4()
        order = SimpleNamespace(id=uuid4(), event_id=uuid4(), status=OrderStatus.DRAFT, expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
        participant = SimpleNamespace(status="reserved", shirt_size_id=size_id)
        inventory = SimpleNamespace(size_id=size_id, reserved=0)
        db = SimpleNamespace(
            execute=AsyncMock(side_effect=[FakeResult([order]), FakeResult([participant]), FakeResult([inventory]), FakeResult(value=None)]),
            add=Mock(),
            commit=AsyncMock(),
        )

        result = await HariSantriService.expire_due_hari_santri_orders(db, request_id="worker-run-2")

        self.assertEqual(0, inventory.reserved)
        self.assertEqual([str(order.id)], result["inventory_mismatch_orders"])
        audit = next(call.args[0] for call in db.add.call_args_list if isinstance(call.args[0], HariSantriAuditLog))
        self.assertEqual("order_reservation_expiry_inventory_mismatch", audit.action)

    async def test_failed_verified_callback_outbox_payload_is_sanitized_and_audited(self):
        event_id = "payment-event-1"
        payload = {
            "event_id": event_id,
            "event_type": "payment.paid",
            "customer": {"email": "private@example.test"},
            "data": {
                "reference_id": "HS-ORDER-1", "event_id": "event-1", "payment_id": "pay-1",
                "amount": 10000, "currency": "IDR", "status": "PAID", "payment_no": "PMT-1",
                "payer_email": "private@example.test",
            },
        }
        settings = SimpleNamespace(HARI_SANTRI_OUTBOX_MAX_ATTEMPTS=8, HARI_SANTRI_OUTBOX_RETRY_BASE_SECONDS=30)
        db = SimpleNamespace(execute=AsyncMock(return_value=FakeResult(value=None)), add=Mock(), flush=AsyncMock(), commit=AsyncMock())
        with patch("app.modules.hari_santri.service.get_settings", return_value=settings):
            event = await HariSantriService.enqueue_failed_callback(
                db, payload=payload, event_id=event_id, payload_hash="a" * 64,
                request_id="request-trace-1", error_code="DatabaseUnavailable", retryable=True,
            )

        self.assertEqual("pending", event.status)
        self.assertEqual("request-trace-1", event.request_id)
        self.assertNotIn("private@example.test", str(event.payload))
        self.assertEqual(1, event.attempts)
        audit = next(call.args[0] for call in db.add.call_args_list if isinstance(call.args[0], HariSantriAuditLog))
        self.assertEqual("payment_callback_failed", audit.action)
        self.assertEqual("request-trace-1", audit.payload["request_id"])
        self.assertTrue(any(isinstance(call.args[0], HariSantriOutboxEvent) for call in db.add.call_args_list))

    async def test_late_paid_callback_after_expiry_needs_review_without_consuming_new_stock(self):
        size_id = uuid4()
        participant = SimpleNamespace(id=uuid4(), order_id=uuid4(), shirt_size_id=size_id, status="expired")
        event_id = "provider-event-late-paid"
        order = SimpleNamespace(id=participant.order_id, event_id=uuid4(), status=OrderStatus.EXPIRED)
        payment = SimpleNamespace(id=uuid4(), order_id=order.id, reference_id="HS-ORDER-LATE", payment_portal_id="pay-1", payment_no=None, amount=10000, currency="IDR", status="EXPIRED")
        inventory = SimpleNamespace(size_id=size_id, reserved=3, allocated=0)
        payload = {
            "event_id": event_id,
            "event_type": "payment.paid",
            "data": {"reference_id": payment.reference_id, "event_id": str(order.event_id), "payment_id": "pay-1", "amount": 10000, "currency": "IDR", "status": "PAID"},
        }
        db = SimpleNamespace(
            execute=AsyncMock(side_effect=[
                FakeResult(), FakeResult(value=None), FakeResult(value=payment), FakeResult(value=order),
                FakeResult([participant]), FakeResult([inventory]),
            ]),
            add=Mock(), commit=AsyncMock(),
        )
        with patch("app.modules.hari_santri.service.get_settings", return_value=SimpleNamespace(APP_SECRET_KEY="test-secret")):
            result = await HariSantriService.process_verified_payment_callback(db, payload, event_id, "b" * 64, "callback-request-1")

        self.assertEqual("paid_needs_review", result["order_status"])
        self.assertEqual(3, inventory.reserved)
        self.assertEqual("expired", participant.status)

    async def test_reconciliation_rejects_payment_from_wrong_service(self):
        order = SimpleNamespace(id=uuid4(), event_id=uuid4(), status=OrderStatus.EXPIRED)
        payment = SimpleNamespace(
            id=uuid4(), order_id=order.id, payment_portal_id="portal-pay-1",
            reference_id="HS-RECON-1", amount=10000, currency="IDR",
        )
        event = SimpleNamespace(slug=HariSantriService.EVENT_SLUG)
        db = SimpleNamespace(
            execute=AsyncMock(side_effect=[FakeResult(value=payment), FakeResult(value=order)]),
            get=AsyncMock(return_value=event),
            commit=AsyncMock(),
        )
        remote = {
            "payment_id": "portal-pay-1", "service_code": "OTHER_SERVICE",
            "reference_id": "HS-RECON-1", "amount": 10000, "currency": "IDR",
            "event_id": str(order.event_id), "status": "PAID",
        }
        with patch("app.modules.hari_santri.service.PaymentPortalClient.get_payment", AsyncMock(return_value=remote)):
            with self.assertRaises(ConflictException) as caught:
                await HariSantriService.reconcile_payment(db, payment.reference_id, uuid4(), "admin-request")
        self.assertEqual("PAYMENT_PORTAL_SERVICE_MISMATCH", caught.exception.code)
        db.commit.assert_not_awaited()

    async def test_reconciliation_rejects_remote_amount_currency_reference_and_event_mismatches(self):
        cases = [
            ("reference_id", "HS-OTHER", "PAYMENT_PORTAL_REFERENCE_MISMATCH"),
            ("amount", 9999, "PAYMENT_PORTAL_AMOUNT_MISMATCH"),
            ("currency", "USD", "PAYMENT_PORTAL_CURRENCY_MISMATCH"),
            ("event_id", str(uuid4()), "PAYMENT_PORTAL_EVENT_MISMATCH"),
        ]
        for key, wrong_value, expected_code in cases:
            with self.subTest(field=key):
                order = SimpleNamespace(id=uuid4(), event_id=uuid4(), status=OrderStatus.PENDING)
                payment = SimpleNamespace(
                    id=uuid4(), order_id=order.id, payment_portal_id="portal-pay-2",
                    reference_id="HS-RECON-2", amount=10000, currency="IDR",
                )
                event = SimpleNamespace(slug=HariSantriService.EVENT_SLUG)
                db = SimpleNamespace(
                    execute=AsyncMock(side_effect=[FakeResult(value=payment), FakeResult(value=order)]),
                    get=AsyncMock(return_value=event), commit=AsyncMock(),
                )
                remote = {
                    "payment_id": "portal-pay-2", "service_code": "HARI_SANTRI_2026",
                    "reference_id": payment.reference_id, "amount": 10000, "currency": "IDR",
                    "event_id": str(order.event_id), "status": "PAID",
                }
                remote[key] = wrong_value
                with patch("app.modules.hari_santri.service.PaymentPortalClient.get_payment", AsyncMock(return_value=remote)):
                    with self.assertRaises(ConflictException) as caught:
                        await HariSantriService.reconcile_payment(db, payment.reference_id, uuid4(), "reconcile-test")
                self.assertEqual(expected_code, caught.exception.code)
                db.commit.assert_not_awaited()

    async def test_reconciliation_missing_reference_is_not_found(self):
        db = SimpleNamespace(execute=AsyncMock(return_value=FakeResult(value=None)))
        with self.assertRaises(NotFoundException):
            await HariSantriService.reconcile_payment(db, "HS-UNKNOWN", uuid4(), "reconcile-missing")

    async def test_paid_lookup_flows_through_callback_transition_with_request_audit(self):
        order = SimpleNamespace(id=uuid4(), event_id=uuid4(), status=OrderStatus.EXPIRED)
        payment = SimpleNamespace(
            id=uuid4(), order_id=order.id, payment_portal_id="portal-pay-late",
            reference_id="HS-LATE-1", amount=10000, currency="IDR",
        )
        event = SimpleNamespace(slug=HariSantriService.EVENT_SLUG)
        db = SimpleNamespace(
            execute=AsyncMock(side_effect=[FakeResult(value=payment), FakeResult(value=order)]),
            get=AsyncMock(return_value=event), add=Mock(), commit=AsyncMock(),
        )
        remote = {
            "payment_id": payment.payment_portal_id, "service_code": "HARI_SANTRI_2026",
            "reference_id": payment.reference_id, "amount": 10000, "currency": "IDR",
            "event_id": str(order.event_id), "status": "PAID",
        }
        with patch("app.modules.hari_santri.service.PaymentPortalClient.get_payment", AsyncMock(return_value=remote)), patch(
            "app.modules.hari_santri.service.HariSantriService.process_verified_payment_callback",
            AsyncMock(return_value={"duplicate": False, "order_status": "paid_needs_review"}),
        ) as apply_transition:
            result = await HariSantriService.reconcile_payment(db, payment.reference_id, uuid4(), "admin-reconcile-7")

        self.assertEqual("paid_needs_review", result["order_status"])
        self.assertTrue(result["reconciled"])
        callback_payload = apply_transition.await_args.args[1]
        self.assertEqual("payment.paid", callback_payload["event_type"])
        self.assertEqual(str(order.event_id), callback_payload["data"]["event_id"])
        self.assertEqual("admin-reconcile-7", apply_transition.await_args.kwargs["request_id"])
        self.assertEqual(1, db.commit.await_count)


if __name__ == "__main__":
    unittest.main()
