from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    APP_NAME: str = "Hari Santri 2026 Event Portal"
    APP_ENV: str = "development"
    APP_DEBUG: bool = False
    API_PREFIX: str = "/api/v1"
    CORS_ENABLED: bool = True

    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/hari_santri"
    APP_SECRET_KEY: str = "change_me"
    JWT_SECRET_KEY: str = "change_me"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    FRONTEND_URL: str = "http://localhost:3000"
    UPLOAD_DIR: str = "uploads"
    UPLOAD_URL_PREFIX: str = "/uploads"
    PROFILE_PHOTO_MAX_SIZE_BYTES: int = 5 * 1024 * 1024

    PROJECT_TIMEZONE: str = "Asia/Jakarta"
    PUBLIC_BASE_URL: str = "http://localhost:8000"
    EMAIL_ENABLED: bool = False
    EMAIL_SMTP_HOST: str = "smtp.titan.email"
    EMAIL_SMTP_PORT: int = 465
    EMAIL_SMTP_USE_SSL: bool = True
    EMAIL_SMTP_USE_TLS: bool = False
    EMAIL_SMTP_USERNAME: str = "event@iwbif.id"
    EMAIL_SMTP_PASSWORD: str = ""
    EMAIL_FROM_ADDRESS: str = "event@iwbif.id"
    EMAIL_FROM_NAME: str = "IWBIF 2026"
    # Optional explicit routes. When omitted, email links are derived from
    # FRONTEND_URL so production cannot silently fall back to localhost.
    FRONTEND_LOGIN_URL: str = ""
    FRONTEND_RESET_PASSWORD_URL: str = ""
    PASSWORD_RESET_EXPIRE_MINUTES: int = 30

    # Legacy provider settings are compatibility-only for historical reports.
    # They are not used by the public API; checkout is delegated to fastapi-bayar.
    DOKU_CLIENT_ID: str = ""
    DOKU_SECRET_KEY: str = ""
    DOKU_BASE_URL: str = "https://api-sandbox.doku.com"
    DOKU_CHECKOUT_PATH: str = "/checkout/v1/payment"
    DOKU_PAYMENT_DUE_MINUTES: int = 60
    DOKU_NOTIFICATION_PATH: str = "/api/v1/webhooks/doku"
    DOKU_NOTIFICATION_BASE_URL: str = ""
    DOKU_CALLBACK_URL: str = "http://localhost:3000/payment/result"

    # Midtrans Snap. Keep SERVER_KEY in the deployment secret store only.
    MIDTRANS_SERVER_KEY: str = ""
    MIDTRANS_CLIENT_KEY: str = ""
    MIDTRANS_IS_PRODUCTION: bool = False
    MIDTRANS_PAYMENT_DUE_MINUTES: int = 60
    MIDTRANS_CALLBACK_URL: str = "http://localhost:3000/payment/result"

    # Organizer-approved fixed conversion and conservative QRIS split point.
    # USD 500 * IDR 18,000 = IDR 9,000,000, below BI's IDR 10,000,000 cap.
    PAYMENT_USD_TO_IDR_RATE: int = 18_000
    QRIS_SEGMENT_LIMIT_IDR: int = 9_000_000
    HARI_SANTRI_RESERVATION_MINUTES: int = 60
    VOUCHER_SCAN_RATE_LIMIT_PER_MINUTE: int = 30
    VOUCHER_SCAN_RATE_LIMIT_WINDOW_SECONDS: int = 60
    PAYMENT_PORTAL_BASE_URL: str = ""
    PAYMENT_PORTAL_CLIENT_ID: str = ""
    PAYMENT_PORTAL_CLIENT_SECRET: str = ""
    PAYMENT_PORTAL_CALLBACK_SECRET: str = ""
    PAYMENT_PORTAL_SERVICE_CODE: str = "HARI_SANTRI_2026"
    PAYMENT_PORTAL_RETURN_URL: str = ""
    PAYMENT_PORTAL_TIMEOUT_SECONDS: int = 15
    PAYMENT_PORTAL_CALLBACK_TOLERANCE_SECONDS: int = 300

    # DOKU Direct API (BI-SNAP). Keys are file paths; never commit private keys.
    DOKU_SNAP_PARTNER_ID: str = ""
    DOKU_SNAP_CLIENT_SECRET: str = ""
    DOKU_SNAP_PRIVATE_KEY_PATH: str = ""
    DOKU_SNAP_DOKU_PUBLIC_KEY_PATH: str = ""
    DOKU_SNAP_DOKU_CLIENT_ID: str = ""
    DOKU_SNAP_VA_CHANNELS_JSON: str = "{}"
    DOKU_SNAP_CHANNEL_ID: str = "H2H"
    DOKU_SNAP_TOKEN_PATH: str = "/authorization/v1/access-token/b2b"
    DOKU_SNAP_VA_CREATE_PATH: str = "/virtual-accounts/bi-snap-va/v1.1/transfer-va/create-va"
    DOKU_SNAP_MERCHANT_TOKEN_PATH: str = "/api/v1/doku/snap/authorization/v1/access-token/b2b"
    DOKU_SNAP_VA_NOTIFICATION_PATH: str = "/api/v1/webhooks/doku/snap/va/payment"
    DOKU_QRIS_MERCHANT_ID: str = ""
    DOKU_QRIS_TERMINAL_ID: str = ""
    DOKU_SNAP_QRIS_GENERATE_PATH: str = "/snap-adapter/b2b/v1.0/qr/qr-mpm-generate"
    DOKU_SNAP_QRIS_NOTIFICATION_PATH: str = "/api/v1/webhooks/doku/snap/qris/payment"
    DOKU_SNAP_EWALLET_CHANNELS_JSON: str = "{}"
    DOKU_SNAP_EWALLET_AUTHORIZATION_RETURN_PATH: str = "/api/v1/payments/doku/snap/e-wallet/authorization/return"
    DOKU_SNAP_EWALLET_NOTIFICATION_PATH: str = "/api/v1/webhooks/doku/snap/e-wallet/payment"
    # Per-channel credentials for SNAP Direct Debit. Keep secrets only in the
    # deployment secret store, never in source control.
    DOKU_SNAP_DIRECT_DEBIT_CHANNELS_JSON: str = "{}"
    DOKU_SNAP_DIRECT_DEBIT_BINDING_RETURN_PATH: str = "/api/v1/payments/doku/snap/direct-debit/binding/return"
    DOKU_SNAP_DIRECT_DEBIT_NOTIFICATION_PATH: str = "/api/v1/webhooks/doku/snap/direct-debit/payment"
    DOKU_SNAP_TOKEN_TTL_SECONDS: int = 900
    DOKU_SNAP_TIMESTAMP_TOLERANCE_SECONDS: int = 300

    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=True,
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
