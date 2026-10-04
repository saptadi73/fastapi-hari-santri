"""Retired legacy payment-channel seeder.

Payment methods are now owned by fastapi-bayar. This file is intentionally
kept as a migration marker so old deployment instructions fail safely instead
of recreating DOKU/Midtrans channels in the event portal database.
"""
import asyncio
async def main() -> None:
    raise SystemExit(
        "Legacy payment-channel seeder is retired. Configure payment methods in fastapi-bayar."
    )


if __name__ == "__main__":
    asyncio.run(main())
