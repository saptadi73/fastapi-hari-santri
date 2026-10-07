"""Safe deployment preflight for Payment Portal settings; never prints secrets."""

from urllib.parse import urlparse

from app.core.config import get_settings


def main() -> int:
    settings = get_settings()
    required = {
        "PAYMENT_PORTAL_BASE_URL": settings.PAYMENT_PORTAL_BASE_URL,
        "PAYMENT_PORTAL_CLIENT_ID": settings.PAYMENT_PORTAL_CLIENT_ID,
        "PAYMENT_PORTAL_CLIENT_SECRET": settings.PAYMENT_PORTAL_CLIENT_SECRET,
        "PAYMENT_PORTAL_CALLBACK_SECRET": settings.PAYMENT_PORTAL_CALLBACK_SECRET,
        "PAYMENT_PORTAL_RETURN_URL": settings.PAYMENT_PORTAL_RETURN_URL,
    }
    missing = [name for name, value in required.items() if not value]
    production = settings.APP_ENV.lower() in {"prod", "production"}
    urls = {
        "PAYMENT_PORTAL_BASE_URL": settings.PAYMENT_PORTAL_BASE_URL,
        "PAYMENT_PORTAL_RETURN_URL": settings.PAYMENT_PORTAL_RETURN_URL,
        "PUBLIC_BASE_URL (callback host)": settings.PUBLIC_BASE_URL,
    }
    invalid_urls = [
        name for name, value in urls.items()
        if value and (not urlparse(value).netloc or (production and urlparse(value).scheme != "https"))
    ]
    print(f"APP_ENV: {settings.APP_ENV}")
    print(f"Service code: {'OK' if settings.PAYMENT_PORTAL_SERVICE_CODE == 'HARI_SANTRI_2026' else 'INVALID'}")
    for name, value in required.items():
        print(f"{name}: {'configured' if value else 'MISSING'}")
    for name in invalid_urls:
        print(f"URL invalid: {name} (production requires HTTPS)")
    callback_path = f"{settings.API_PREFIX.rstrip('/')}/integrations/payment-portal/callback"
    print(f"Callback URL to register: {settings.PUBLIC_BASE_URL.rstrip('/')}{callback_path}")
    print("OAuth scopes requested by backend: payments:read payments:write")
    if missing or invalid_urls or settings.PAYMENT_PORTAL_SERVICE_CODE != "HARI_SANTRI_2026":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
