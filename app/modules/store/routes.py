from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user, get_db_session, require_admin
from app.modules.payments.models import Order
from app.modules.store import schemas
from app.modules.store.models import Product
from app.modules.store.models import OrderItem
from app.modules.events.models import Event
from app.modules.email_notifications.service import deliver_to_user
from app.modules.store.service import StoreService
from app.modules.users.models import User
from app.support.responses import success_response
from app.core.i18n import request_locale
from app.modules.content_translations.service import localize_models, translation_map

router = APIRouter(prefix="/store", tags=["store"])


@router.get("/events/{event_id}/products")
async def products(event_id: UUID, request: Request, db: AsyncSession = Depends(get_db_session)):
    rows = (await db.execute(select(Product).where(Product.event_id == event_id, Product.is_active.is_(True)).order_by(Product.product_type, Product.name))).scalars().all()
    data = await localize_models(db, "product", rows, request_locale(request))
    return success_response("Product ditemukan", data, request=request)


@router.get("/events/{event_id}/exhibitor-availability/me")
async def my_exhibitor_availability(event_id: UUID, request: Request, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db_session)):
    data = await StoreService.exhibitor_availability(db, user.id, event_id)
    return success_response("Status exhibitor package ditemukan", data=data, request=request)


@router.get("/events/{event_id}/additional-products/me")
async def my_additional_products(event_id: UUID, request: Request, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db_session)):
    rows = await StoreService.additional_availability(db, user.id, event_id)
    translations = await translation_map(db, "product", [product.id for product, _, _, _ in rows], request_locale(request))
    data = []
    for product, state, order, registration in rows:
        values = schemas.ProductRead.model_validate(product).model_dump()
        translation = translations.get(product.id)
        if translation:
            values["name"] = translation.fields.get("name", values["name"])
            values["description"] = translation.fields.get("description", values["description"])
        data.append(schemas.AdditionalProductAvailability(
            **values,
            purchase_status=state,
            is_purchasable=state == "available",
            existing_order_id=order.id if order else None,
            registration_id=registration.id if registration else None,
            reason=None if state == "available" else state,
        ))
    return success_response("Status additional package ditemukan", data=data, request=request)


@router.post("/admin/events/{event_id}/products", status_code=201)
async def create_product(event_id: UUID, payload: schemas.ProductWrite, request: Request, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db_session)):
    row = Product(event_id=event_id, **payload.model_dump())
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return success_response("Product berhasil dibuat", schemas.ProductRead.model_validate(row), request=request)


@router.get("/admin/events/{event_id}/products")
async def admin_products(event_id: UUID, request: Request, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db_session)):
    rows = (await db.execute(
        select(Product).where(Product.event_id == event_id).order_by(Product.product_type, Product.name)
    )).scalars().all()
    return success_response("Product admin ditemukan", [schemas.ProductRead.model_validate(row) for row in rows], request=request)


@router.put("/admin/products/{product_id}")
async def update_product(product_id: UUID, payload: schemas.ProductWrite, request: Request, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db_session)):
    row = await db.get(Product, product_id)
    if not row:
        from app.core.exceptions import NotFoundException
        raise NotFoundException("PRODUCT_NOT_FOUND", "Product tidak ditemukan")
    for key, value in payload.model_dump().items():
        setattr(row, key, value)
    await db.commit()
    await db.refresh(row)
    return success_response("Product berhasil diperbarui", schemas.ProductRead.model_validate(row), request=request)


@router.delete("/admin/products/{product_id}")
async def delete_product(product_id: UUID, request: Request, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db_session)):
    row = await db.get(Product, product_id)
    if not row:
        from app.core.exceptions import NotFoundException
        raise NotFoundException("PRODUCT_NOT_FOUND", "Product tidak ditemukan")
    await db.delete(row)
    await db.commit()
    return success_response("Product berhasil dihapus", {"id": product_id}, request=request)


@router.get("/events/{event_id}/cart")
async def get_cart(event_id: UUID, request: Request, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db_session)):
    cart, rows = await StoreService.get_cart(db, user.id, event_id)
    translations = await translation_map(db, "product", [product.id for _, product in rows], request_locale(request))
    items = [schemas.CartItemRead(id=item.id, product_id=product.id, code=product.code, name=translations.get(product.id).fields.get("name", product.name) if translations.get(product.id) else product.name, product_type=product.product_type, quantity=item.quantity, unit_price=float(product.price), currency=product.currency, line_total=float(product.price) * item.quantity) for item, product in rows]
    return success_response("Cart ditemukan", schemas.CartRead(id=cart.id, event_id=event_id, items=items, subtotal=sum(item.line_total for item in items), currency=items[0].currency if items else None), request=request)


@router.post("/events/{event_id}/cart/items")
async def add_cart_item(event_id: UUID, payload: schemas.CartItemWrite, request: Request, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db_session)):
    cart, rows = await StoreService.add_item(db, user.id, event_id, payload)
    translations = await translation_map(db, "product", [product.id for _, product in rows], request_locale(request))
    items = [schemas.CartItemRead(id=item.id, product_id=product.id, code=product.code, name=translations.get(product.id).fields.get("name", product.name) if translations.get(product.id) else product.name, product_type=product.product_type, quantity=item.quantity, unit_price=float(product.price), currency=product.currency, line_total=float(product.price) * item.quantity) for item, product in rows]
    return success_response("Item berhasil ditambahkan ke cart", schemas.CartRead(id=cart.id, event_id=event_id, items=items, subtotal=sum(item.line_total for item in items), currency=items[0].currency if items else None), request=request)


@router.delete("/events/{event_id}/cart/items/{product_id}")
async def remove_cart_item(event_id: UUID, product_id: UUID, request: Request, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db_session)):
    cart, rows = await StoreService.remove_item(db, user.id, event_id, product_id)
    return success_response("Item berhasil dihapus dari cart", {"cart_id": cart.id, "items": len(rows)}, request=request)


@router.post("/events/{event_id}/checkout")
async def checkout(event_id: UUID, request: Request, background_tasks: BackgroundTasks, payload: schemas.CheckoutConsent | None = None, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db_session)):
    order, item_count = await StoreService.checkout(db, user.id, event_id, request_locale(request), payload.terms_accepted if payload else None)
    event = await db.get(Event, event_id)
    items = (await db.execute(select(OrderItem).where(OrderItem.order_id == order.id, OrderItem.product_type == "exhibitor"))).scalars().all()
    for item in items:
        background_tasks.add_task(deliver_to_user, event_id, "exhibitor_package_selected", user.id, {"event_name": event.name, "package_name": item.product_name, "package_code": item.product_code, "amount": item.line_total, "currency": item.currency, "_product_id": item.product_id}, "order", order.id)
    return success_response("Order berhasil dibuat dan menunggu pembayaran", schemas.CheckoutRead(order_id=order.id, order_number=order.order_number, total_amount=float(order.total_amount), currency=order.currency, status=order.status, item_count=item_count, created_at=order.created_at, order_kind=order.order_kind), request=request)
