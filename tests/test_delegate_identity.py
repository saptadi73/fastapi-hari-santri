import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from app.modules.iwbif.models import DelegateRegistrationDetail
from app.modules.iwbif.schemas import DelegateRegistrationWrite
from app.modules.iwbif.service import IwbifService


class DelegateIdentityTests(unittest.IsolatedAsyncioTestCase):
    def test_documented_payload_and_legacy_identity(self):
        payload = {
            "job_title": "Director",
            "company_organization": "Example Company",
            "business_sector": "Technology",
            "company_address": "Example address",
            "participation_categories": ["Delegate"],
            "room_preference": "Twin Sharing",
            "arrival_date": "2026-10-14",
            "departure_date": "2026-10-17",
            "airport": "CGK",
            "need_airport_pickup": False,
            "products_services": "Software services",
            "looking_for": ["Buyer"],
            "preferred_countries": ["Indonesia"],
            "business_objectives": "Find partners",
            "activity_ids": [str(uuid4())],
            "need_official_invoice": False,
            "information_accuracy_confirmed": True,
            "terms_accepted": True,
            "business_matching_data_consent": True,
            "terms_version": "v1",
            "consent_version": "v1",
        }
        clean = DelegateRegistrationWrite(**payload).model_dump()
        fields = {"full_name", "title", "nationality", "email"}
        legacy = DelegateRegistrationWrite(**payload, **dict.fromkeys(fields, "ignored")).model_dump()
        self.assertEqual(clean, legacy)
        self.assertTrue(fields.isdisjoint(DelegateRegistrationWrite.model_json_schema()["properties"]))

    def test_response_hides_legacy_identity(self):
        detail = DelegateRegistrationDetail(full_name="Old name", email="old@example.com", title="Ms", nationality="Indonesian", job_title="Director")
        reg = SimpleNamespace(id=uuid4(), event_id=uuid4(), participant_id=uuid4(), registration_number="TEST", status="draft")
        result = IwbifService.serialize_registration(reg, detail)["detail"]
        self.assertEqual(result["job_title"], "Director")
        self.assertTrue({"full_name", "title", "nationality", "email"}.isdisjoint(result))

    async def test_participant_without_account_name_uses_email(self):
        db = AsyncMock()
        db.add = MagicMock()
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        db.execute.return_value = result
        db.get.return_value = SimpleNamespace(id=uuid4(), full_name=None, email="account@example.com")
        participant = await IwbifService.resolve_participant(db, db.get.return_value.id)
        self.assertEqual(participant.full_name, "account@example.com")
