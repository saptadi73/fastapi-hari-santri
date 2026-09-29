"""Exercise HTTP search/pagination against real SQL queries in an isolated database."""
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
import asyncio
import json
from urllib.parse import urlencode
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.main import app
from app.core.database import Base
from app.core.dependencies import get_db_session, require_admin
from app.modules.admin_content.models import Announcement, Certificate
from app.modules.check_ins.models import CheckIn
from app.modules.committee.models import CommitteeMember
from app.modules.email_notifications.models import EmailNotificationLog
from app.modules.events.models import Event
from app.modules.content_translations.models import ContentTranslation
from app.modules.participants.models import ParticipantProfile
from app.modules.iwbif.models import DelegatePackage
from app.modules.store.models import Product, OrderItem
from app.modules.payments.models import Order, Payment
from app.modules.registrations.models import Registration
from app.modules.sessions.models import EventSession
from app.modules.speakers.models import Speaker
from app.modules.tickets.models import Ticket
from app.modules.users.models import User


class ASGIClient:
    def __init__(self, application):
        self.application = application

    def get(self, path, params):
        async def request():
            messages = []
            async def receive():
                return {"type": "http.request", "body": b"", "more_body": False}
            async def send(message):
                messages.append(message)
            await self.application({
                "type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
                "http_version": "1.1", "method": "GET", "scheme": "http",
                "path": path, "raw_path": path.encode(), "query_string": urlencode(params).encode(),
                "headers": [], "client": ("127.0.0.1", 12345), "server": ("test", 80), "root_path": "",
            }, receive, send)
            status = next(m["status"] for m in messages if m["type"] == "http.response.start")
            body = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
            return SimpleNamespace(status_code=status, text=body.decode(), json=lambda: json.loads(body))
        return asyncio.run(request())


class AsyncSessionAdapter:
    def __init__(self, session):
        self.session = session

    async def execute(self, statement):
        return self.session.execute(statement)

    async def scalar(self, statement):
        return self.session.scalar(statement)

    async def get(self, model, key):
        return self.session.get(model, key)


@pytest.fixture
def api():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = Session(engine)
    now = datetime.now(timezone.utc)
    event_id = uuid4()
    session.add(Event(id=event_id, name="Main", slug="main", start_at=now, end_at=now))
    product_id = uuid4()
    session.add_all([
        DelegatePackage(event_id=event_id, code="VIP", name="Premium Delegate", amount=100),
        Product(id=product_id, event_id=event_id, code="DELEGATE_VIP", name="Premium Delegate", product_type="delegate", price=100),
    ])
    for i in range(3):
        user_id, participant_id, registration_id, ticket_id, order_id = [uuid4() for _ in range(5)]
        session.add_all([
            User(id=user_id, full_name=f"Alice {i}", email=f"alice{i}@example.com", phone=f"+62888{i}", country="Indonesia", password_hash="unused"),
            ParticipantProfile(id=participant_id, user_id=user_id, full_name=f"Participant {i}", organization_name="Acme"),
            Registration(id=registration_id, event_id=event_id, participant_id=participant_id, registration_number=f"REG-{i}", status="confirmed"),
            Ticket(id=ticket_id, registration_id=registration_id, ticket_number=f"TKT-{i}"),
            Order(id=order_id, user_id=user_id, registration_id=registration_id, order_number=f"ORDER-{i}", total_amount=100),
            OrderItem(order_id=order_id, product_id=product_id, product_code="DELEGATE_VIP", product_name="Premium Delegate", product_type="delegate", quantity=1, unit_price=100, currency="USD", line_total=100),
            Payment(order_id=order_id, provider="midtrans" if i == 0 else "doku", provider_reference_no=f"GATEWAY-{i}", channel_code="BANK", gross_amount=100, transaction_status="success"),
            Speaker(full_name=f"Speaker {i}", organization_name="100% Acme" if i == 0 else "Acme"),
            Announcement(event_id=event_id, title=f"Notice {i}", body="Welcome everyone", status="published"),
            Certificate(event_id=event_id, user_id=user_id, certificate_number=f"CERT-{i}", title="Attendance Award"),
            CommitteeMember(event_id=event_id, full_name=f"Committee {i}", role_title="Chair", committee_group="Advisory", organization_name="Acme"),
            EventSession(event_id=event_id, title=f"Talk {i}", slug=f"talk-{i}", session_type="Panel", room_name="Ballroom", status="scheduled", start_at=now, end_at=now),
            EmailNotificationLog(event_id=event_id, trigger="registration", recipient=f"alice{i}@example.com", subject="Welcome", status="failed" if i == 0 else "sent"),
        ])
        if i == 0:
            session.add(CheckIn(event_id=event_id, ticket_id=ticket_id, check_in_at=now, gate_name="North Gate"))
    session.add(Announcement(event_id=uuid4(), title="Other event", body="Welcome everyone", status="published"))
    session.flush()
    for model, entity, field in [
        (Speaker, "speaker", "organization_name"),
        (CommitteeMember, "committee_member", "role_title"),
        (EventSession, "session", "title"),
        (Announcement, "announcement", "body"),
        (Certificate, "certificate", "title"),
    ]:
        for row in session.scalars(select(model)).all():
            for locale in ["en", "id"]:
                session.add(ContentTranslation(entity_type=entity, entity_id=row.id, locale=locale,
                    fields={field: "Bahasa Indonesia 100%_matched"}))
    session.commit()
    previous = app.dependency_overrides.copy()
    app.dependency_overrides[get_db_session] = lambda: AsyncSessionAdapter(session)
    app.dependency_overrides[require_admin] = lambda: SimpleNamespace(id=uuid4(), role="admin")
    client = ASGIClient(app)
    yield client, event_id
    app.dependency_overrides.clear()
    app.dependency_overrides.update(previous)
    session.close()
    engine.dispose()


def get(api, path, **params):
    client, event_id = api
    response = client.get("/api/v1" + path.format(event_id=event_id), params=params)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize("path,terms", [
    ("/admin/users", ["ALICE", "example.com", "+62888", "indonesia"]),
    ("/speakers", ["speaker", "acme"]),
    ("/admin/events/{event_id}/announcements", ["notice", "welcome", "published"]),
    ("/admin/events/{event_id}/certificates", ["cert-", "award", "alice", "example.com"]),
    ("/admin/committee", ["committee", "chair", "advisory", "acme"]),
    ("/events/main/sessions", ["talk", "panel", "ballroom", "scheduled"]),
    ("/admin/events/{event_id}/email-notifications/logs/history", ["alice", "welcome", "registration"]),
])
def test_list_search_before_pagination(api, path, terms):
    for term in terms:
        result = get(api, path, event_id=str(api[1]), search=f" {term} ", page=2, size=2)
        assert len(result["data"]) == 1
        assert result["meta"] == {"page": 2, "size": 2, "total": 3, "pages": 2}
    empty = get(api, path, event_id=str(api[1]), search="missing", page=1, size=2)
    assert empty["data"] == []
    assert empty["meta"]["pages"] == 0
    past_end = get(api, path, event_id=str(api[1]), page=5, size=2)
    assert past_end["data"] == []
    assert past_end["meta"]["total"] == 3


def test_literal_search_blank_search_and_combined_filters(api):
    assert get(api, "/speakers", search="% Acme", size=1)["meta"]["total"] == 1
    assert get(api, "/speakers", search="_Acme", size=1)["meta"]["total"] == 0
    assert get(api, "/speakers", search="   ", size=1)["meta"]["total"] == 3
    assert get(api, "/admin/users", search="alice", role="organizer")["meta"]["total"] == 0
    logs = "/admin/events/{event_id}/email-notifications/logs/history"
    assert get(api, logs, search="alice", status="failed", page=1, size=2)["meta"]["total"] == 1
    assert get(api, logs, search="alice", locale="id")["meta"]["total"] == 0
    assert len(get(api, logs, limit=1)["data"]) == 1


@pytest.mark.parametrize("path,total", [("/admin/transactions", 3), ("/admin/reports/payments", 2), ("/admin/reports/payments/midtrans", 1)])
def test_payment_search_and_summary(api, path, total):
    for term in ["order-", "gateway-", "participant", "bank", "success", "example.com", "reg-", "premium", "vip"]:
        result = get(api, path, search=term, page=1, size=1)
        assert result["meta"]["total"] == total
        assert result["meta"]["pages"] == total
        assert len(result["data"]["transactions"]) == 1
        assert result["data"]["summary"]["total_transactions"] == total
    result = get(api, path, search="missing", page=1, size=1)
    assert result["data"]["transactions"] == []
    assert result["data"]["summary"]["gross_revenue"] == 0


def test_payment_legacy_pagination_and_filter_precedence(api):
    result = get(api, "/admin/transactions", limit=1, offset=1)
    assert len(result["data"]["transactions"]) == 1
    assert result["meta"]["offset"] == 1
    result = get(api, "/admin/transactions", page=1, size=2, limit=1, offset=100)
    assert len(result["data"]["transactions"]) == 2
    assert result["meta"]["offset"] == 0
    assert get(api, "/admin/transactions", search="midtrans", provider="doku")["meta"]["total"] == 0


def test_attendance_search_and_pagination(api):
    path = "/attendance/events/{event_id}/report"
    for term in ["participant", "reg-", "tkt-", "acme", "confirmed"]:
        result = get(api, path, search=term, page=2, size=2)
        assert result["data"]["registrants"] == result["data"]["attendees"]
        assert len(result["data"]["attendees"]) == 1
        assert result["meta"] == {"page": 2, "size": 2, "total": 3, "pages": 2}
        assert result["data"]["summary"]["total_registered"] == 3
    assert get(api, path, search="north")["meta"]["total"] == 1
    assert get(api, path, search="not_checked_in")["meta"]["total"] == 2
    assert get(api, path, search="missing")["data"]["attendees"] == []


@pytest.mark.parametrize("path", ["/admin/transactions", "/admin/users", "/admin/reports/payments", "/admin/reports/payments/midtrans", "/speakers", "/admin/events/{event_id}/announcements", "/admin/events/{event_id}/certificates", "/admin/committee", "/events/main/sessions", "/attendance/events/{event_id}/report", "/admin/events/{event_id}/email-notifications/logs/history"])
def test_invalid_pagination(api, path):
    for params in [{"page": 0}, {"size": 0}, {"size": 501}]:
        response = api[0].get("/api/v1" + path.format(event_id=api[1]), params={"event_id": str(api[1]), **params})
        assert response.status_code == 422


@pytest.mark.parametrize("path", [
    "/speakers", "/admin/committee", "/events/main/sessions",
    "/admin/events/{event_id}/announcements", "/admin/events/{event_id}/certificates",
])
def test_translation_search_counts_entities_not_translations(api, path):
    for term in ["Bahasa", "100%_matched"]:
        result = get(api, path, event_id=str(api[1]), search=term, page=2, size=2, locale="id")
        assert len(result["data"]) == 1
        assert result["meta"] == {"page": 2, "size": 2, "total": 3, "pages": 2}
    empty = get(api, path, event_id=str(api[1]), search="100%_missing", page=1, size=2)
    assert empty["meta"]["total"] == 0
