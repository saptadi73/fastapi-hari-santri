from fastapi import APIRouter, HTTPException


router = APIRouter(tags=["legacy-payment"])


@router.post("/orders/{order_id}/continue-payment", summary="Legacy payment disabled")
async def continue_legacy_payment(order_id: str):
    """Keep a clear migration response for old clients without calling a provider."""
    raise HTTPException(
        status_code=410,
        detail="Pembayaran langsung legacy dihentikan. Gunakan checkout Hari Santri melalui Payment Portal.",
    )
