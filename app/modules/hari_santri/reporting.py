import csv
import io
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ValidationException
from app.modules.events.models import Event
from app.modules.hari_santri.models import HariSantriDonation, HariSantriPayment
from app.modules.payments.models import Order
from app.modules.users.models import User


PAYMENT_STATUSES = {"CREATING", "CREATED", "PENDING", "UNKNOWN", "PAID", "FAILED", "EXPIRED", "CANCELED"}
DONATION_STATUSES = PAYMENT_STATUSES


def _date_bounds(date_from: date | None, date_to: date | None) -> tuple[datetime | None, datetime | None]:
    start = datetime.combine(date_from, time.min, tzinfo=timezone.utc) if date_from else None
    end = datetime.combine(date_to + timedelta(days=1), time.min, tzinfo=timezone.utc) if date_to else None
    return start, end


def _validate_status(status: str | None, allowed: set[str]) -> str | None:
    normalized = status.strip().upper() if status else None
    if normalized and normalized not in allowed:
        raise ValidationException("INVALID_REPORT_STATUS", "Status laporan tidak valid")
    return normalized


class HariSantriReportingService:
    @staticmethod
    async def donation_rows(
        db: AsyncSession,
        *,
        donor_email: str | None = None,
        event_id: UUID | None = None,
        status: str | None = None,
        search: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> list[dict[str, Any]]:
        normalized_status = _validate_status(status, DONATION_STATUSES)
        start, end = _date_bounds(date_from, date_to)
        stmt = (
            select(HariSantriDonation, Event.name.label("event_name"))
            .join(Event, Event.id == HariSantriDonation.event_id)
            .order_by(HariSantriDonation.created_at.desc(), HariSantriDonation.id.desc())
        )
        if donor_email:
            stmt = stmt.where(func.lower(HariSantriDonation.donor_email) == donor_email.strip().lower())
        if event_id:
            stmt = stmt.where(HariSantriDonation.event_id == event_id)
        if normalized_status:
            stmt = stmt.where(func.upper(HariSantriDonation.status) == normalized_status)
        if start:
            stmt = stmt.where(HariSantriDonation.created_at >= start)
        if end:
            stmt = stmt.where(HariSantriDonation.created_at < end)
        if search and search.strip():
            term = f"%{search.strip()}%"
            stmt = stmt.where(or_(
                HariSantriDonation.reference_id.ilike(term),
                HariSantriDonation.payment_no.ilike(term),
                HariSantriDonation.donor_name.ilike(term),
                HariSantriDonation.donor_email.ilike(term),
            ))
        result = []
        for donation, event_name in (await db.execute(stmt)).all():
            result.append({
                "id": str(donation.id),
                "event_id": str(donation.event_id),
                "event_name": event_name,
                "reference_id": donation.reference_id,
                "donor_name": donation.donor_name,
                "donor_email": donation.donor_email,
                "amount": int(donation.amount),
                "currency": donation.currency,
                "status": donation.status.upper(),
                "payment_no": donation.payment_no,
                "paid_at": donation.paid_at,
                "created_at": donation.created_at,
            })
        return result

    @staticmethod
    async def payment_rows(
        db: AsyncSession,
        *,
        event_id: UUID | None = None,
        status: str | None = None,
        search: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> list[dict[str, Any]]:
        normalized_status = _validate_status(status, PAYMENT_STATUSES)
        start, end = _date_bounds(date_from, date_to)
        stmt = (
            select(HariSantriPayment, Order, User, Event.name.label("event_name"))
            .join(Order, Order.id == HariSantriPayment.order_id)
            .join(User, User.id == Order.user_id)
            .outerjoin(Event, Event.id == Order.event_id)
            .order_by(HariSantriPayment.created_at.desc(), HariSantriPayment.id.desc())
        )
        if event_id:
            stmt = stmt.where(Order.event_id == event_id)
        if normalized_status:
            stmt = stmt.where(func.upper(HariSantriPayment.status) == normalized_status)
        if start:
            stmt = stmt.where(HariSantriPayment.created_at >= start)
        if end:
            stmt = stmt.where(HariSantriPayment.created_at < end)
        if search and search.strip():
            term = f"%{search.strip()}%"
            stmt = stmt.where(or_(
                HariSantriPayment.reference_id.ilike(term),
                HariSantriPayment.payment_no.ilike(term),
                Order.order_number.ilike(term),
                User.full_name.ilike(term),
                User.email.ilike(term),
            ))
        result = []
        for payment, order, user, event_name in (await db.execute(stmt)).all():
            status_value = payment.status.upper()
            result.append({
                "id": str(payment.id),
                "event_id": str(order.event_id) if order.event_id else None,
                "event_name": event_name,
                "order_id": str(order.id),
                "order_number": order.order_number,
                "order_status": order.status,
                "reference_id": payment.reference_id,
                "payment_no": payment.payment_no,
                "customer_name": user.full_name or user.email,
                "customer_email": user.email,
                "amount": int(payment.amount),
                "currency": payment.currency,
                "status": status_value,
                "paid_at": payment.updated_at if status_value == "PAID" else None,
                "created_at": payment.created_at,
            })
        return result

    @staticmethod
    def summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
        paid = [row for row in rows if row["status"] == "PAID"]
        return {
            "total_records": len(rows),
            "paid_records": len(paid),
            "pending_records": sum(row["status"] in {"CREATING", "CREATED", "PENDING", "UNKNOWN"} for row in rows),
            "failed_records": sum(row["status"] in {"FAILED", "EXPIRED", "CANCELED"} for row in rows),
            "total_paid": sum(row["amount"] for row in paid),
            "currency": next((row["currency"] for row in rows), "IDR"),
        }

    @staticmethod
    def page(rows: list[dict[str, Any]], page: int, size: int) -> tuple[list[dict[str, Any]], dict[str, int]]:
        total = len(rows)
        pages = (total + size - 1) // size if total else 0
        start = (page - 1) * size
        return rows[start:start + size], {"page": page, "size": size, "total": total, "pages": pages}

    @staticmethod
    def csv(rows: list[dict[str, Any]], *, kind: str) -> str:
        common = ["event_name", "reference_id", "payment_no", "amount", "currency", "status", "paid_at", "created_at"]
        columns = (
            ["order_number", "order_status", "customer_name", "customer_email", *common]
            if kind == "payments"
            else ["donor_name", "donor_email", *common]
        )
        output = io.StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
        return "\ufeff" + output.getvalue()
