import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from app.core.exceptions import NotFoundException, ValidationException
from app.main import app
from app.modules.content_translations.models import ContentTranslation
from app.modules.content_translations.routes import delete_translation, list_entity_translations
from app.modules.content_translations.schemas import TranslationWrite
from app.modules.content_translations.service import localize_models, upsert, validate_entity_type, validate_fields, validate_locale
from app.modules.events.models import Event, EventStatus
from app.modules.business_matching.models import MeetingResource, MeetingVenue
from app.modules.business_matching.routes import resources as meeting_resources
from app.modules.iwbif.models import DelegatePackage, DelegatePackageFacility, DelegatePackageRate
from app.modules.iwbif.package_service import DelegatePackageService
from app.modules.store.models import Product
from app.modules.store.service import StoreService
from app.core.dependencies import require_admin
from fastapi import HTTPException
from app.modules.users.models import User


class ContentTranslationContractTests(unittest.TestCase):
    def test_admin_translation_routes_are_registered(self):
        paths = app.openapi()["paths"]
        base = "/api/v1/admin/content-translations/{entity_type}/{entity_id}"
        self.assertIn("get", paths[base])
        self.assertIn("put", paths[f"{base}/{{locale}}"])
        self.assertIn("delete", paths[f"{base}/{{locale}}"])

    def test_only_whitelisted_fields_can_be_translated(self):
        validate_fields("event", {"name": "论坛", "description": "说明"})
        with self.assertRaises(ValidationException) as context:
            validate_fields("event", {"status": "published"})
        self.assertEqual("INVALID_TRANSLATION_FIELD", context.exception.code)
        with self.assertRaises(ValidationException) as value_context:
            validate_fields("event", {"name": {"unsafe": "object"}})
        self.assertEqual("INVALID_TRANSLATION_VALUE", value_context.exception.code)

    def test_translation_rejects_blank_values(self):
        with self.assertRaises(ValueError):
            TranslationWrite(fields={"name": "  "})

    def test_translation_ignores_blank_optional_values(self):
        payload = TranslationWrite(fields={
            "professional_title": "主持人",
            "organization_name": "",
            "biography": None,
        })

        self.assertEqual({"professional_title": "主持人"}, payload.fields)

    def test_expertise_tags_must_be_a_list_of_non_empty_strings(self):
        validate_fields("speaker", {"expertise_tags": ["Investment", "Trade"]})
        with self.assertRaises(ValidationException) as not_list:
            validate_fields("speaker", {"expertise_tags": "Investment"})
        self.assertEqual("INVALID_TRANSLATION_VALUE", not_list.exception.code)
        with self.assertRaises(ValidationException) as blank_item:
            validate_fields("speaker", {"expertise_tags": ["Investment", "  "]})
        self.assertEqual("INVALID_TRANSLATION_VALUE", blank_item.exception.code)

    def test_translation_value_rejects_field_exceeding_length_limit(self):
        with self.assertRaises(ValidationException) as context:
            validate_fields("event", {"description": "x" * 20_001})
        self.assertEqual("INVALID_TRANSLATION_VALUE", context.exception.code)

    def test_entity_type_and_locale_are_validated_consistently(self):
        self.assertEqual("session", validate_entity_type("session"))
        self.assertEqual("id", validate_locale("id-ID"))
        with self.assertRaises(ValidationException) as entity_error:
            validate_entity_type("unknown")
        self.assertEqual("INVALID_TRANSLATION_ENTITY", entity_error.exception.code)
        with self.assertRaises(ValidationException) as locale_error:
            validate_locale("zh-Hans")
        self.assertEqual("UNSUPPORTED_LOCALE", locale_error.exception.code)

    def test_checkout_snapshot_keeps_localized_name_and_locale(self):
        product = Product(id=uuid4(), event_id=uuid4(), code="GOLD", name="Gold", product_type="delegate", price=100, currency="USD", metadata_json={"package_code": "GOLD"})
        translation = ContentTranslation(locale="zh-CN", fields={"name": "金牌套餐"})
        name, metadata = StoreService.localized_product_snapshot(product, translation)
        self.assertEqual("金牌套餐", name)
        self.assertEqual("zh-CN", metadata["content_locale"])
        self.assertEqual("GOLD", metadata["package_code"])


class ContentTranslationOverlayTests(unittest.IsolatedAsyncioTestCase):
    async def test_translation_admin_dependency_accepts_organizer_and_rejects_participant(self):
        organizer = User(id=uuid4(), email="organizer@example.com", password_hash="x", country="ID", role="organizer")
        participant = User(id=uuid4(), email="participant@example.com", password_hash="x", country="ID", role="participant")
        self.assertIs(organizer, await require_admin(organizer))
        with self.assertRaises(HTTPException) as context:
            await require_admin(participant)
        self.assertEqual(403, context.exception.status_code)

    async def test_upsert_creates_audited_translation(self):
        entity_id = uuid4()
        admin_id = uuid4()
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        db = AsyncMock()
        db.add = MagicMock()
        db.get.return_value = Event(id=entity_id, name="Forum")
        db.execute.return_value = result

        row = await upsert(db, "event", entity_id, "id", {"name": "Forum Kebersamaan"}, admin_id)

        self.assertEqual("id", row.locale)
        self.assertEqual({"name": "Forum Kebersamaan"}, row.fields)
        self.assertEqual(admin_id, row.created_by)
        db.add.assert_called_once_with(row)
        db.commit.assert_awaited_once()
        db.refresh.assert_awaited_once_with(row)

    async def test_translation_overlays_display_fields_without_changing_machine_fields(self):
        event_id = uuid4()
        event = Event(
            id=event_id,
            name="IWBIF Forum",
            slug="iwbif-forum",
            description="Forum description",
            timezone="Asia/Jakarta",
            start_at=datetime.now(timezone.utc),
            end_at=datetime.now(timezone.utc),
            capacity=100,
            status=EventStatus.PUBLISHED,
        )
        translation = ContentTranslation(
            id=uuid4(),
            entity_type="event",
            entity_id=event_id,
            locale="id",
            fields={"name": "Forum Hari Santri", "description": "Perayaan kebersamaan warga"},
        )
        result = MagicMock()
        result.scalars.return_value.all.return_value = [translation]
        db = AsyncMock()
        db.execute.return_value = result

        localized = (await localize_models(db, "event", [event], "id"))[0]

        self.assertEqual("Forum Hari Santri", localized["name"])
        self.assertEqual("Perayaan kebersamaan warga", localized["description"])
        self.assertEqual("published", localized["status"].value)
        self.assertEqual("iwbif-forum", localized["slug"])
        self.assertEqual("id", localized["content_locale"])
        self.assertFalse(localized["translation_fallback"])

    async def test_missing_chinese_translation_uses_source_and_marks_fallback(self):
        event = Event(
            id=uuid4(), name="Source", slug="source", timezone="Asia/Jakarta",
            start_at=datetime.now(timezone.utc), end_at=datetime.now(timezone.utc),
            capacity=10, status=EventStatus.PUBLISHED,
        )
        result = MagicMock()
        result.scalars.return_value.all.return_value = []
        db = AsyncMock()
        db.execute.return_value = result

        localized = (await localize_models(db, "event", [event], "id"))[0]
        self.assertEqual("Source", localized["name"])
        self.assertEqual("source", localized["content_locale"])
        self.assertTrue(localized["translation_fallback"])

    async def test_list_entity_translations_returns_ordered_translations(self):
        entity_id = uuid4()
        admin = User(id=uuid4(), email="admin@example.com", password_hash="x", country="ID", role="admin")
        rows = [ContentTranslation(id=uuid4(), entity_type="event", entity_id=entity_id, locale="en", fields={"name": "Forum"}, created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc))]
        result = MagicMock()
        result.scalars.return_value.all.return_value = rows
        db = AsyncMock()
        db.get.return_value = Event(id=entity_id, name="Forum")
        db.execute.return_value = result

        response = await list_entity_translations("event", entity_id, request=None, admin=admin, db=db)

        self.assertEqual(1, len(response["data"]))
        self.assertEqual("en", response["data"][0].locale)

    async def test_delete_translation_removes_existing_row(self):
        entity_id = uuid4()
        admin = User(id=uuid4(), email="admin@example.com", password_hash="x", country="ID", role="admin")
        row = ContentTranslation(id=uuid4(), entity_type="event", entity_id=entity_id, locale="id", fields={"name": "x"})
        result = MagicMock()
        result.scalar_one_or_none.return_value = row
        db = AsyncMock()
        db.get.return_value = Event(id=entity_id, name="Forum")
        db.execute.return_value = result

        await delete_translation("event", entity_id, "id", request=None, admin=admin, db=db)

        db.delete.assert_called_once_with(row)
        db.commit.assert_awaited_once()

    async def test_delete_translation_raises_when_missing(self):
        entity_id = uuid4()
        admin = User(id=uuid4(), email="admin@example.com", password_hash="x", country="ID", role="admin")
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        db = AsyncMock()
        db.get.return_value = Event(id=entity_id, name="Forum")
        db.execute.return_value = result

        with self.assertRaises(NotFoundException) as context:
            await delete_translation("event", entity_id, "id", request=None, admin=admin, db=db)
        self.assertEqual("CONTENT_TRANSLATION_NOT_FOUND", context.exception.code)

    async def test_catalog_applies_translation_and_fallback_to_nested_rate_and_facility(self):
        event_id = uuid4()
        package_id = uuid4()
        rate_id = uuid4()
        facility_id = uuid4()
        package = DelegatePackage(id=package_id, event_id=event_id, code="GOLD", name="Gold Package", package_type="main", selection_mode="required_one", description="Gold description", display_order=0, currency="USD", amount=100, is_active=True)
        rate = DelegatePackageRate(id=rate_id, delegate_package_id=package_id, occupancy_type="single", name="Single Occupancy", amount=100, currency="USD", is_default=True, is_active=True)
        facility = DelegatePackageFacility(id=facility_id, delegate_package_id=package_id, name="Airport Pickup", description="Round trip", pricing_mode="included", currency="USD", display_order=0, is_active=True)
        package_translation = ContentTranslation(entity_id=package_id, locale="id", fields={"name": "Paket Emas", "description": "Paket pilihan peserta"})
        facility_translation = ContentTranslation(entity_id=facility_id, locale="id", fields={"name": "Antar jemput"})

        packages_result = MagicMock(); packages_result.scalars.return_value = [package]
        rates_result = MagicMock(); rates_result.scalars.return_value = [rate]
        products_result = MagicMock(); products_result.all.return_value = []
        facilities_result = MagicMock(); facilities_result.scalars.return_value = [facility]
        package_translations_result = MagicMock(); package_translations_result.scalars.return_value.all.return_value = [package_translation]
        rate_translations_result = MagicMock(); rate_translations_result.scalars.return_value.all.return_value = []
        facility_translations_result = MagicMock(); facility_translations_result.scalars.return_value.all.return_value = [facility_translation]

        db = AsyncMock()
        db.execute = AsyncMock(side_effect=[
            packages_result, rates_result, products_result, facilities_result,
            package_translations_result, rate_translations_result, facility_translations_result,
        ])

        catalog = await DelegatePackageService.catalog(db, event_id, admin=False, locale="id")

        package_item = catalog["main_packages"][0]
        self.assertEqual("Paket Emas", package_item["name"])
        self.assertEqual("id", package_item["content_locale"])
        self.assertFalse(package_item["translation_fallback"])
        self.assertEqual("Single Occupancy", package_item["rates"][0]["name"])
        self.assertTrue(package_item["rates"][0]["translation_fallback"])
        self.assertEqual("Antar jemput", package_item["facilities"][0]["name"])
        self.assertFalse(package_item["facilities"][0]["translation_fallback"])

    async def test_meeting_resources_endpoint_merges_localized_venue_name(self):
        event_id = uuid4()
        venue_id = uuid4()
        user = User(id=uuid4(), email="delegate@example.com", password_hash="x", country="ID", role="participant")
        resource = MeetingResource(id=uuid4(), venue_id=venue_id, resource_type="table", code="T1", name="Table 1", capacity=2, is_active=True)
        venue = MeetingVenue(id=venue_id, event_id=event_id, name="Grand Ballroom", location_description="Level 3")
        venue_translation = ContentTranslation(entity_id=venue_id, locale="id", fields={"name": "Aula utama"})

        resource_translations_result = MagicMock(); resource_translations_result.scalars.return_value.all.return_value = []
        venues_result = MagicMock(); venues_result.scalars.return_value = [venue]
        venue_translations_result = MagicMock(); venue_translations_result.scalars.return_value.all.return_value = [venue_translation]

        db = AsyncMock()
        db.execute = AsyncMock(side_effect=[resource_translations_result, venues_result, venue_translations_result])

        with patch("app.modules.business_matching.routes.Service.context", new=AsyncMock(return_value=None)), \
             patch("app.modules.business_matching.routes.Repo.resources", new=AsyncMock(return_value=[resource])):
            fake_request = SimpleNamespace(query_params={"locale": "id"}, headers={}, state=SimpleNamespace())
            response = await meeting_resources(event_id, request=fake_request, user=user, db=db)

        item = response["data"][0]
        self.assertEqual("Table 1", item["name"])
        self.assertEqual("Aula utama", item["venue_name"])


if __name__ == "__main__":
    unittest.main()
