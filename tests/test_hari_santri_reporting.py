import csv
import io
from datetime import datetime, timezone

from app.modules.hari_santri.reporting import HariSantriReportingService
from app.modules.participants.reporting import ParticipantReportingService


def test_summary_counts_paid_pending_and_failed_records():
    rows = [
        {"status": "PAID", "amount": 75000, "currency": "IDR"},
        {"status": "PENDING", "amount": 25000, "currency": "IDR"},
        {"status": "UNKNOWN", "amount": 10000, "currency": "IDR"},
        {"status": "FAILED", "amount": 5000, "currency": "IDR"},
    ]
    summary = HariSantriReportingService.summary(rows)
    assert summary == {
        "total_records": 4,
        "paid_records": 1,
        "pending_records": 2,
        "failed_records": 1,
        "total_paid": 75000,
        "currency": "IDR",
    }


def test_donation_csv_contains_donor_and_payment_identity():
    now = datetime.now(timezone.utc)
    content = HariSantriReportingService.csv([{
        "event_name": "Harlah NU ke-101",
        "reference_id": "DON-001",
        "payment_no": "PAY-001",
        "donor_name": "Siti Rahma",
        "donor_email": "siti@example.com",
        "amount": 25000,
        "currency": "IDR",
        "status": "PAID",
        "paid_at": now,
        "created_at": now,
    }], kind="donations")
    row = next(csv.DictReader(io.StringIO(content.lstrip("\ufeff"))))
    assert row["donor_email"] == "siti@example.com"
    assert row["reference_id"] == "DON-001"
    assert row["status"] == "PAID"


def test_report_pagination_is_stable():
    rows = [{"status": "PAID", "amount": number, "currency": "IDR"} for number in range(5)]
    items, meta = HariSantriReportingService.page(rows, page=2, size=2)
    assert [item["amount"] for item in items] == [2, 3]
    assert meta == {"page": 2, "size": 2, "total": 5, "pages": 3}


def test_participant_report_normalizes_payment_portal_status():
    paid_at = datetime.now(timezone.utc)
    payment = type("Payment", (), {"status": "PAID", "updated_at": paid_at})()
    assert ParticipantReportingService._payment_status(payment) == "success"
    assert ParticipantReportingService._payment_paid_at(payment) == paid_at.isoformat()
