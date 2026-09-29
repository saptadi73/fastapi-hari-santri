import unittest

from app.modules.email_notifications.schemas import AccountPreferenceRead, AccountPreferenceWrite
from app.modules.email_notifications.service import DEFAULT_TEMPLATES, TRIGGER_VARIABLES, apply_content_translations, render, select_delivery_template
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4


class EmailNotificationTemplateTests(unittest.TestCase):
    def test_every_trigger_has_default_and_declared_variables(self):
        self.assertEqual(set(DEFAULT_TEMPLATES), set(TRIGGER_VARIABLES))

    def test_render_replaces_known_variables_and_blanks_missing_values(self):
        rendered = render("Halo {{ participant_name }}, paket {{ package_name }} / {{ missing }}", {"participant_name": "Saptadi", "package_name": "Gold"})
        self.assertEqual("Halo Saptadi, paket Gold / ", rendered)

    def test_business_matching_and_commercial_triggers_are_available(self):
        expected = {
            "account_registered",
            "registration_submitted",
            "delegate_package_selected",
            "exhibitor_package_selected",
            "payment_confirmed",
            "business_matching_profile_saved",
            "meeting_requested",
            "meeting_accepted",
            "meeting_confirmed",
            "meeting_declined",
            "meeting_cancelled",
            "meeting_reschedule_requested",
        }
        self.assertEqual(expected, set(TRIGGER_VARIABLES))

    def test_account_preference_supports_override_and_restore_default(self):
        self.assertFalse(AccountPreferenceWrite(is_enabled=False).is_enabled)
        self.assertIsNone(AccountPreferenceWrite(is_enabled=None).is_enabled)

        preference = AccountPreferenceRead(
            event_id=uuid4(),
            user_id=uuid4(),
            trigger="payment_confirmed",
            global_enabled=True,
            override_enabled=False,
            effective_enabled=False,
        )
        self.assertFalse(preference.effective_enabled)

    def test_dynamic_email_variables_use_recipient_locale_translations(self):
        translated = apply_content_translations(
            {"event_name": "Forum", "package_name": "Gold - Single", "meeting_venue": "Table A"},
            event_translation=SimpleNamespace(fields={"name": "商务论坛"}),
            package_translation=SimpleNamespace(fields={"name": "金牌套餐"}),
            rate_translation=SimpleNamespace(fields={"name": "单人房"}),
            resource_translation=SimpleNamespace(fields={"name": "A号桌"}),
        )
        self.assertEqual("商务论坛", translated["event_name"])
        self.assertEqual("金牌套餐 - 单人房", translated["package_name"])
        self.assertEqual("A号桌", translated["meeting_venue"])


class EmailTemplateFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_indonesian_template_falls_back_to_enabled_english(self):
        english = SimpleNamespace(locale="en", is_enabled=True)
        result = MagicMock()
        result.scalars.return_value.all.return_value = [english]
        db = AsyncMock()
        db.execute.return_value = result
        selected = await select_delivery_template(db, uuid4(), "payment_confirmed", "id")
        self.assertIs(english, selected)

    async def test_disabled_requested_template_does_not_bypass_to_english(self):
        indonesian = SimpleNamespace(locale="id", is_enabled=False)
        english = SimpleNamespace(locale="en", is_enabled=True)
        result = MagicMock()
        result.scalars.return_value.all.return_value = [indonesian, english]
        db = AsyncMock()
        db.execute.return_value = result
        selected = await select_delivery_template(db, uuid4(), "payment_confirmed", "id")
        self.assertIsNone(selected)
