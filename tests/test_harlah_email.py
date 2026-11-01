from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from app.core.email import (
    format_currency_amount,
    send_harlah_donation_confirmation,
    send_harlah_payment_confirmation,
)


def test_format_currency_amount_formats_idr_decimal_exponent() -> None:
    assert format_currency_amount(Decimal("1E+4"), "IDR") == "Rp10.000"


def test_format_currency_amount_keeps_generic_currency_code() -> None:
    assert format_currency_amount(Decimal("10000.50"), "USD") == "USD 10,000.50"


@pytest.mark.asyncio
async def test_donation_confirmation_uses_human_readable_amount() -> None:
    with patch("app.core.email.send_harlah_confirmation", new=AsyncMock(return_value=True)) as sender:
        sent = await send_harlah_donation_confirmation(
            "donor@example.test", "Donatur", "DON-HS26-TEST", Decimal("1E+4"), "IDR"
        )

    assert sent is True
    assert "Jumlah: Rp10.000" in sender.await_args.kwargs["body"]
    assert "1E+4" not in sender.await_args.kwargs["body"]


@pytest.mark.asyncio
async def test_payment_confirmation_uses_human_readable_amount() -> None:
    with patch("app.core.email.send_harlah_confirmation", new=AsyncMock(return_value=True)) as sender:
        sent = await send_harlah_payment_confirmation(
            "participant@example.test", "Peserta", "HS26-TEST", Decimal("7.5E+4"), "IDR"
        )

    assert sent is True
    assert "Jumlah: Rp75.000" in sender.await_args.kwargs["body"]
    assert "E+" not in sender.await_args.kwargs["body"]
