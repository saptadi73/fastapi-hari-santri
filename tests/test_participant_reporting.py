import csv
import io
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.core.exceptions import ValidationException
from app.modules.participants.reporting import _origin_region, profile_status, PROFILE_FIELDS
from app.modules.participants.reporting_routes import _rows, participant_report

from app.modules.participants.reporting import ParticipantReportingService


def test_participant_csv_keeps_multiple_packages_as_separate_rows():
    rows = [{
        "participant_id": "participant-1",
        "full_name": "Participant One",
        "email": "one@example.com",
        "phone": None,
        "country": "Indonesia",
        "organization_name": "Example Org",
        "packages": [
            {"package_code": "A", "package_name": "Package A", "payment_status": "success"},
            {"package_code": "C", "package_name": "Package C", "payment_status": "pending"},
        ],
    }]

    exported = list(csv.DictReader(io.StringIO(ParticipantReportingService.csv(rows))))

    assert len(exported) == 2
    assert [row["package_code"] for row in exported] == ["A", "C"]
    assert [row["payment_status"] for row in exported] == ["success", "pending"]
    assert all(row["participant_id"] == "participant-1" for row in exported)


def test_origin_region_uses_village_district_and_regency_names():
    user = SimpleNamespace(
        province_code="32",
        regency_code="3216",
        district_code="321606",
        village_code="3216062001",
    )
    regions = {
        "32": SimpleNamespace(name="Jawa Barat"),
        "3216": SimpleNamespace(name="Kabupaten Bekasi"),
        "321606": SimpleNamespace(name="Tarumajaya"),
        "3216062001": SimpleNamespace(name="Setia Asih"),
    }

    result = _origin_region(user, regions)

    assert result["origin_region"] == "Setia Asih, Tarumajaya, Kabupaten Bekasi"
    assert result["village_name"] == "Setia Asih"
    assert result["district_name"] == "Tarumajaya"
    assert result["regency_name"] == "Kabupaten Bekasi"


@pytest.mark.parametrize('values, expected', [
    ({}, 'not_started'),
    ({'full_name': 'Name', 'biography': '  '}, 'not_started'),
    ({'full_name': 'Name', 'organization_name': 'Org'}, 'partial'),
    ({'profile_photo_url': '/photo.jpg'}, 'partial'),
    ({'full_name': 'Name', 'organization_name': 'Org', 'biography': 'Partnership', 'profile_photo_url': None}, 'complete'),
    ({'full_name': 'Name', 'organization_name': 'Org', 'biography': 'Partnership', 'profile_photo_url': '  '}, 'complete'),
    ({field: 'filled' for field in PROFILE_FIELDS}, 'complete'),
])
def test_profile_completion(values, expected):
    result = profile_status(SimpleNamespace(**values))
    assert result['profile_status'] == expected
    assert result['profile_missing_fields'] == [field for field in PROFILE_FIELDS if not values.get(field, '').strip()]


def test_report_includes_missing_profiles_and_filters_before_pagination():
    complete = SimpleNamespace(id=uuid4(), **{field: 'filled' for field in PROFILE_FIELDS})
    partial = SimpleNamespace(id=uuid4(), full_name='Name', organization_name='Org')
    created_at = datetime(2026, 10, 31, 10, 0, tzinfo=timezone.utc)
    users = [SimpleNamespace(
        id=uuid4(), full_name='Account name', email=f'{i}@example.com', phone=None,
        country='ID', registration_status='account_created', created_at=created_at,
        province_code=None, regency_code=None, district_code=None, village_code=None,
    ) for i in range(3)]

    def database():
        return SimpleNamespace(execute=AsyncMock(side_effect=[
            SimpleNamespace(all=lambda: [(None, users[0]), (partial, users[1]), (complete, users[2])]),
            SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [])),
        ]))

    rows = asyncio.run(ParticipantReportingService.rows(database()))
    assert [row['profile_status'] for row in rows] == ['not_started', 'partial', 'complete']
    assert rows[0]['participant_id'] is None
    assert rows[0]['full_name'] == 'Account name'
    assert rows[0]['registered_at'] == created_at.isoformat()
    assert rows[0]['account_created_at'] == created_at.isoformat()
    assert rows[0]['origin_region'] is None
    filtered = asyncio.run(_rows(database(), None, None, None, None, 'complete'))
    assert len(filtered) == 1
    exported = list(csv.DictReader(io.StringIO(ParticipantReportingService.csv(filtered))))
    assert exported[0]['profile_status'] == 'complete'
    assert exported[0]['profile_missing_fields'] == ''
    response = asyncio.run(participant_report(request=None, event_id=None, package_id=None, payment_status=None, search=None, profile_status='complete', page=1, size=1, admin=None, db=database()))
    assert response['meta']['total'] == 1
    assert response['data'][0]['profile_status'] == 'complete'


def test_invalid_profile_filter_is_rejected():
    with pytest.raises(ValidationException):
        asyncio.run(_rows(None, None, None, None, None, 'invalid'))


def test_csv_repeats_profile_status_for_each_package():
    rows = [{'profile_status': 'partial', 'profile_missing_fields': ['biography'], 'packages': [{'package_code': 'A'}, {'package_code': 'B'}]}]
    exported = list(csv.DictReader(io.StringIO(ParticipantReportingService.csv(rows))))
    assert len(exported) == 2
    assert all(row['profile_status'] == 'partial' for row in exported)
    assert all(row['profile_missing_fields'] == 'biography' for row in exported)
