from fastapi import APIRouter

from app.modules.events import routes as event_routes
from app.modules.health import routes as health_routes
from app.modules.identity import routes as identity_routes
from app.modules.check_ins import routes as checkin_routes
from app.modules.attendance import routes as attendance_routes
from app.modules.participants import routes as participant_routes
from app.modules.participants import reporting_routes as participant_reporting_routes
from app.modules.speakers import routes as speaker_routes
from app.modules.sessions import routes as session_routes
from app.modules.tickets import routes as ticket_routes
from app.modules.registrations import routes as registration_routes
from app.modules.business_matching import routes as business_matching_routes
from app.modules.iwbif import routes as iwbif_routes
from app.modules.store import routes as store_routes
from app.modules.admin_content import routes as admin_content_routes
from app.modules.users import admin_routes as user_admin_routes
from app.modules.email_notifications import routes as email_notification_routes
from app.modules.content_translations import routes as content_translation_routes
from app.modules.committee import routes as committee_routes
from app.modules.hari_santri import routes as hari_santri_routes
from app.modules.regions import routes as region_routes
from app.modules.payments import routes as payment_routes
from app.api.legacy_payment_guard import router as legacy_payment_guard_router

router = APIRouter()

# Only preserve read-only order/invoice/report routes from the historical
# payment module. Direct provider checkout, manual/offline confirmation, and
# provider webhooks are intentionally not exposed by the application router;
# Hari Santri checkout uses the Payment Portal routes instead.
_PAYMENT_READONLY_PATHS = {
    "/payments/{payment_id}",
    "/orders/{order_id}",
    "/orders",
    "/orders/{order_id}/detail",
    "/payments/registrations/{registration_ref}/invoice",
    "/payments/me/invoices",
    "/admin/transactions",
    "/admin/reports/payments/midtrans",
    "/admin/reports/payments/midtrans.csv",
    "/admin/reports/payments/manual",
    "/admin/reports/payments/manual.csv",
    "/admin/reports/payments",
    "/admin/reports/payments.csv",
}
payment_readonly_router = APIRouter()
payment_readonly_router.routes.extend(
    route for route in payment_routes.router.routes
    if getattr(route, "path", "") in _PAYMENT_READONLY_PATHS
)

router.include_router(health_routes.router)
router.include_router(payment_readonly_router)
router.include_router(legacy_payment_guard_router)
router.include_router(identity_routes.router)
router.include_router(event_routes.router, prefix="/events", tags=["events"])
router.include_router(hari_santri_routes.router)
router.include_router(region_routes.router)
router.include_router(participant_routes.router, tags=["participants"])
router.include_router(participant_reporting_routes.router)
router.include_router(checkin_routes.router)
router.include_router(attendance_routes.router)
router.include_router(speaker_routes.router)
router.include_router(session_routes.router)
router.include_router(ticket_routes.router)
router.include_router(registration_routes.router, tags=["registrations"])
router.include_router(business_matching_routes.router, tags=["business-matching"])
router.include_router(iwbif_routes.router, tags=["iwbif-2026"])
router.include_router(store_routes.router)
router.include_router(admin_content_routes.router)
router.include_router(user_admin_routes.router)
router.include_router(email_notification_routes.router)
router.include_router(content_translation_routes.router)
router.include_router(committee_routes.public_router)
router.include_router(committee_routes.admin_router)
