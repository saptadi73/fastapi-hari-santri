import asyncio
import html
import logging
import smtplib
from email.message import EmailMessage
from urllib.parse import urlencode

from app.core.config import get_settings

logger = logging.getLogger(__name__)


def _send_message(
    message: EmailMessage,
    host: str,
    port: int,
    username: str,
    password: str,
    use_ssl: bool,
    use_tls: bool,
) -> None:
    smtp_class = smtplib.SMTP_SSL if use_ssl else smtplib.SMTP
    with smtp_class(host, port, timeout=30) as client:
        client.ehlo()
        if use_tls and not use_ssl:
            client.starttls()
            client.ehlo()
        client.login(username, password)
        client.send_message(message)


async def send_registration_confirmation(email: str) -> None:
    settings = get_settings()
    if not settings.EMAIL_ENABLED:
        logger.info("Registration email disabled; recipient=%s", email)
        return
    if not settings.EMAIL_SMTP_PASSWORD:
        logger.error("Registration email skipped: EMAIL_SMTP_PASSWORD is empty")
        return

    login_url = frontend_login_url()
    message = EmailMessage()
    message["Subject"] = "Registrasi IWBIF 2026 berhasil"
    message["From"] = f"{settings.EMAIL_FROM_NAME} <{settings.EMAIL_FROM_ADDRESS}>"
    message["To"] = email
    message.set_content(
        "Registrasi Anda berhasil dilakukan pada event IWBIF 2026.\n\n"
        f"Silakan login untuk melanjutkan pendaftaran dan memilih package:\n{login_url}\n\n"
        "Salam,\nIWBIF 2026"
    )
    try:
        await asyncio.to_thread(
            _send_message,
            message,
            settings.EMAIL_SMTP_HOST,
            settings.EMAIL_SMTP_PORT,
            settings.EMAIL_SMTP_USERNAME,
            settings.EMAIL_SMTP_PASSWORD,
            settings.EMAIL_SMTP_USE_SSL,
            settings.EMAIL_SMTP_USE_TLS,
        )
    except Exception:
        logger.exception("Failed to send registration email; recipient=%s", email)


async def send_harlah_confirmation(
    email: str,
    *,
    subject: str,
    body: str,
) -> bool:
    """Send a Harlah NU transactional thank-you email."""
    settings = get_settings()
    if not settings.EMAIL_ENABLED or not settings.EMAIL_SMTP_PASSWORD:
        logger.error("Harlah email skipped: SMTP is not configured; recipient=%s", email)
        return False
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = f"{settings.EMAIL_FROM_NAME} <{settings.EMAIL_FROM_ADDRESS}>"
    message["To"] = email
    message.set_content(body)
    try:
        await asyncio.to_thread(
            _send_message,
            message,
            settings.EMAIL_SMTP_HOST,
            settings.EMAIL_SMTP_PORT,
            settings.EMAIL_SMTP_USERNAME,
            settings.EMAIL_SMTP_PASSWORD,
            settings.EMAIL_SMTP_USE_SSL,
            settings.EMAIL_SMTP_USE_TLS,
        )
        return True
    except Exception:
        logger.exception("Failed to send Harlah email; recipient=%s", email)
        return False


async def send_harlah_payment_confirmation(email: str, participant_name: str, order_number: str, amount: object, currency: str) -> bool:
    return await send_harlah_confirmation(
        email,
        subject=f"Terima kasih, pembayaran {order_number} berhasil",
        body=(
            f"Assalamu’alaikum {participant_name},\n\n"
            "Terima kasih. Pembayaran pendaftaran Harlah NU ke-101 telah berhasil dikonfirmasi.\n\n"
            f"Nomor pesanan: {order_number}\nJumlah: {currency} {amount}\n\n"
            "Silakan masuk ke dashboard untuk melihat tiket digital Anda.\n\n"
            "Salam,\nPanitia Harlah NU ke-101"
        ),
    )


async def send_harlah_donation_confirmation(email: str, donor_name: str, reference_id: str, amount: object, currency: str) -> bool:
    return await send_harlah_confirmation(
        email,
        subject="Terima kasih atas donasi Anda untuk Harlah NU ke-101",
        body=(
            f"Assalamu’alaikum {donor_name},\n\n"
            "Terima kasih atas dukungan dan donasi Anda untuk kegiatan Harlah NU ke-101.\n\n"
            f"Referensi donasi: {reference_id}\nJumlah: {currency} {amount}\n\n"
            "Semoga menjadi amal baik dan membawa keberkahan.\n\n"
            "Salam,\nPanitia Harlah NU ke-101"
        ),
    )


def _frontend_route(configured_url: str, path: str) -> str:
    settings = get_settings()
    configured_url = configured_url.strip()
    if configured_url:
        return configured_url
    # FRONTEND_URL also supplies the comma-separated CORS allow-list. Email
    # links must use one origin, never the entire allow-list as a URL.
    primary_frontend_url = next(
        (
            origin.strip().rstrip("/")
            for origin in settings.FRONTEND_URL.split(",")
            if origin.strip()
        ),
        "",
    )
    return f"{primary_frontend_url}{path}"


def frontend_login_url() -> str:
    settings = get_settings()
    return _frontend_route(settings.FRONTEND_LOGIN_URL, "/auth/login")


def password_reset_url(token: str) -> str:
    settings = get_settings()
    reset_url = _frontend_route(settings.FRONTEND_RESET_PASSWORD_URL, "/auth/reset-password")
    separator = "&" if "?" in reset_url else "?"
    return f"{reset_url}{separator}{urlencode({'token': token})}"


async def send_password_reset_email(email: str, token: str) -> bool:
    settings = get_settings()
    if not settings.EMAIL_ENABLED:
        logger.info("Password reset email disabled; recipient=%s", email)
        return False
    if not settings.EMAIL_SMTP_PASSWORD:
        logger.error("Password reset email skipped: EMAIL_SMTP_PASSWORD is empty")
        return False

    reset_url = password_reset_url(token)
    message = EmailMessage()
    message["Subject"] = "Atur ulang kata sandi akun Harlah NU"
    message["From"] = f"{settings.EMAIL_FROM_NAME} <{settings.EMAIL_FROM_ADDRESS}>"
    message["To"] = email
    message.set_content(
        "Assalamu'alaikum,\n\n"
        "Kami menerima permintaan untuk mengatur ulang kata sandi akun Harlah NU Anda.\n\n"
        f"Buka tautan berikut untuk membuat kata sandi baru:\n{reset_url}\n\n"
        f"Tautan ini berlaku selama {settings.PASSWORD_RESET_EXPIRE_MINUTES} menit dan hanya dapat digunakan satu kali.\n"
        "Jika Anda tidak meminta perubahan ini, abaikan email ini. Kata sandi Anda tetap aman.\n\n"
        "Salam,\nPanitia Harlah NU ke-101"
    )
    safe_reset_url = html.escape(reset_url, quote=True)
    message.add_alternative(
        f"""\
<!doctype html>
<html lang="id">
  <body style="margin:0;background:#f3f5ef;font-family:Arial,sans-serif;color:#173f32">
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#f3f5ef;padding:32px 12px">
      <tr><td align="center">
        <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:600px;background:#ffffff;border-radius:18px;overflow:hidden;box-shadow:0 14px 40px rgba(23,63,50,.12)">
          <tr><td style="padding:28px 32px;background:linear-gradient(135deg,#0b5d38,#173f32);color:#ffffff">
            <div style="color:#f8c341;font-size:12px;font-weight:700;letter-spacing:2px">HARLAH NU KE-101</div>
            <h1 style="margin:12px 0 0;font-size:30px;line-height:1.2">Atur ulang kata sandi</h1>
          </td></tr>
          <tr><td style="padding:32px">
            <p style="margin:0 0 16px;line-height:1.7">Assalamu'alaikum,</p>
            <p style="margin:0 0 24px;line-height:1.7;color:#425b52">Kami menerima permintaan untuk mengatur ulang kata sandi akun Harlah NU Anda.</p>
            <p style="margin:0 0 28px;text-align:center">
              <a href="{safe_reset_url}" style="display:inline-block;border-radius:999px;background:#f8b817;color:#173f32;font-weight:700;text-decoration:none;padding:14px 24px">Buat kata sandi baru</a>
            </p>
            <p style="margin:0 0 12px;line-height:1.7;color:#425b52">Tautan berlaku selama <strong>{settings.PASSWORD_RESET_EXPIRE_MINUTES} menit</strong> dan hanya dapat digunakan satu kali.</p>
            <p style="margin:0;line-height:1.7;color:#687a73;font-size:13px">Jika Anda tidak meminta perubahan ini, abaikan email ini. Kata sandi Anda tetap aman.</p>
          </td></tr>
          <tr><td style="padding:20px 32px;background:#edf3ea;color:#425b52;font-size:12px">Panitia Harlah NU ke-101</td></tr>
        </table>
      </td></tr>
    </table>
  </body>
</html>""",
        subtype="html",
    )
    try:
        await asyncio.to_thread(
            _send_message,
            message,
            settings.EMAIL_SMTP_HOST,
            settings.EMAIL_SMTP_PORT,
            settings.EMAIL_SMTP_USERNAME,
            settings.EMAIL_SMTP_PASSWORD,
            settings.EMAIL_SMTP_USE_SSL,
            settings.EMAIL_SMTP_USE_TLS,
        )
        return True
    except Exception:
        logger.exception("Failed to send password reset email; recipient=%s", email)
        return False
