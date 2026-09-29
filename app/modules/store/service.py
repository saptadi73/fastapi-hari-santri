import uuid
from decimal import Decimal

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictException, NotFoundException, ValidationException
from app.modules.payments.models import Order, OrderKind, OrderStatus
from app.modules.store.models import Cart, CartItem, OrderItem, Product
from app.modules.events.models import Event
from app.modules.iwbif.models import DelegatePackage, DelegatePackageRate, DelegateRegistrationPackageSelection, ExhibitorRegistration
from app.modules.users.models import User
from app.modules.participants.models import ParticipantProfile
from app.modules.registrations.models import Registration, RegistrationStatus


class StoreService:
    ACTIVE_ORDER_STATUSES = {OrderStatus.DRAFT, OrderStatus.PENDING, OrderStatus.PARTIALLY_PAID, OrderStatus.PAID}

    @staticmethod
    async def exhibitor_availability(db: AsyncSession, user_id, event_id):
        order = (await db.execute(
            select(Order).join(OrderItem, OrderItem.order_id == Order.id).where(
                Order.user_id == user_id, Order.event_id == event_id,
                Order.status.in_(StoreService.ACTIVE_ORDER_STATUSES),
                OrderItem.product_type == "exhibitor",
            ).order_by(Order.created_at.desc()).limit(1)
        )).scalars().first()
        registration_id = (await db.execute(
            select(ExhibitorRegistration.id)
            .join(ParticipantProfile, ParticipantProfile.id == ExhibitorRegistration.participant_id)
            .where(ParticipantProfile.user_id == user_id, ExhibitorRegistration.event_id == event_id)
            .limit(1)
        )).scalar_one_or_none()
        return {
            "is_purchasable": order is None and registration_id is None,
            "existing_order_id": order.id if order else None,
            "order_status": order.status if order else None,
            "exhibitor_id": registration_id,
        }

    @staticmethod
    async def _require_exhibitor_available(db, user_id, event_id):
        # Serialize purchases for this owner, including requests from different tabs.
        await db.execute(select(User.id).where(User.id == user_id).with_for_update())
        state = await StoreService.exhibitor_availability(db, user_id, event_id)
        if not state["is_purchasable"]:
            raise ConflictException(
                "EXHIBITOR_PACKAGE_ALREADY_SELECTED",
                "Anda sudah terdaftar atau memiliki pesanan exhibitor. Lengkapi data dan pembayaran pesanan yang sudah ada.",
            )

    @staticmethod
    async def _active_registration(db: AsyncSession, user_id, event_id, *, lock: bool = False):
        stmt = (
            select(Registration)
            .join(ParticipantProfile, ParticipantProfile.id == Registration.participant_id)
            .where(
                ParticipantProfile.user_id == user_id,
                Registration.event_id == event_id,
                Registration.status.notin_([RegistrationStatus.CANCELED, RegistrationStatus.CANCELLED]),
            )
        )
        if lock:
            stmt = stmt.with_for_update()
        return (await db.execute(stmt)).scalar_one_or_none()

    @staticmethod
    async def _paid_main_order_exists(db: AsyncSession, registration_id) -> bool:
        return bool((await db.execute(
            select(Order.id)
            .join(OrderItem, OrderItem.order_id == Order.id)
            .where(
                Order.registration_id == registration_id,
                Order.status == OrderStatus.PAID,
                OrderItem.product_type == "delegate",
            ).limit(1)
        )).scalar_one_or_none())

    @staticmethod
    async def _additional_purchase_state(db: AsyncSession, user_id, event_id, package_id, registration=None):
        registration = registration or await StoreService._active_registration(db, user_id, event_id)
        if registration:
            owned = (await db.execute(select(DelegateRegistrationPackageSelection.id).where(
                DelegateRegistrationPackageSelection.registration_id == registration.id,
                DelegateRegistrationPackageSelection.delegate_package_id == package_id,
            ).limit(1))).scalar_one_or_none()
            if owned:
                return "owned", None, registration
        order = (await db.execute(
            select(Order)
            .join(OrderItem, OrderItem.order_id == Order.id)
            .join(Product, Product.id == OrderItem.product_id)
            .join(DelegatePackageRate, DelegatePackageRate.id == Product.delegate_package_rate_id)
            .where(
                Order.user_id == user_id,
                Order.event_id == event_id,
                Order.status.in_(StoreService.ACTIVE_ORDER_STATUSES),
                DelegatePackageRate.delegate_package_id == package_id,
                Product.product_type == "additional",
            )
            .order_by(Order.created_at.desc())
        )).scalars().first()
        if order:
            state = "owned" if order.status == OrderStatus.PAID else order.status
            return state, order, registration
        if not registration:
            return "registration_required", None, None
        if not await StoreService._paid_main_order_exists(db, registration.id):
            return "main_payment_required", None, registration
        return "available", None, registration

    @staticmethod
    async def additional_availability(db: AsyncSession, user_id, event_id):
        products = list((await db.execute(select(Product).where(
            Product.event_id == event_id,
            Product.product_type == "additional",
            Product.is_active.is_(True),
        ).order_by(Product.name))).scalars())
        registration = await StoreService._active_registration(db, user_id, event_id)
        result = []
        for product in products:
            rate = await db.get(DelegatePackageRate, product.delegate_package_rate_id) if product.delegate_package_rate_id else None
            state, order, resolved_registration = await StoreService._additional_purchase_state(
                db, user_id, event_id, rate.delegate_package_id if rate else None, registration,
            ) if rate else ("unavailable", None, registration)
            result.append((product, state, order, resolved_registration))
        return result

    @staticmethod
    def localized_product_snapshot(product, translation):
        metadata = dict(product.metadata_json or {})
        metadata["content_locale"] = translation.locale if translation else "source"
        name = translation.fields.get("name", product.name) if translation else product.name
        return name, metadata

    @staticmethod
    async def get_cart(db: AsyncSession, user_id, event_id):
        cart = (await db.execute(select(Cart).where(Cart.user_id == user_id, Cart.event_id == event_id))).scalar_one_or_none()
        if not cart:
            cart = Cart(user_id=user_id, event_id=event_id)
            db.add(cart)
            await db.flush()
        rows = (await db.execute(select(CartItem, Product).join(Product, Product.id == CartItem.product_id).where(CartItem.cart_id == cart.id).order_by(Product.product_type, Product.name))).all()
        return cart, rows

    @staticmethod
    async def add_item(db: AsyncSession, user_id, event_id, payload):
        product = await db.get(Product, payload.product_id)
        if not product or product.event_id != event_id or not product.is_active:
            raise NotFoundException("PRODUCT_NOT_FOUND", "Product tidak ditemukan atau tidak aktif")
        event = await db.get(Event, event_id)
        is_hari_santri = bool(event and getattr(event, "slug", None) == "hari-santri-2026")
        if is_hari_santri and product.product_type == "hari_santri_package":
            activity_type = (product.metadata_json or {}).get("activity_type")
            if activity_type not in {"CYCLING", "FAMILY_WALK"} or payload.quantity != 1:
                raise ValidationException("INVALID_HARI_SANTRI_PACKAGE", "Pilih tepat satu paket Hari Santri")
            await db.execute(select(User.id).where(User.id == user_id).with_for_update())
        if product.product_type == "exhibitor":
            await StoreService._require_exhibitor_available(db, user_id, event_id)
            if payload.quantity != 1:
                raise ValidationException("PACKAGE_QUANTITY_INVALID", "Exhibitor package hanya dapat dipilih satu kali")
        cart, rows = await StoreService.get_cart(db, user_id, event_id)
        if is_hari_santri and product.product_type == "hari_santri_package":
            existing_hari_santri_ids = [
                item.id for item, selected in rows
                if selected.product_type == "hari_santri_package" and selected.id != product.id
            ]
            if existing_hari_santri_ids:
                await db.execute(delete(CartItem).where(CartItem.id.in_(existing_hari_santri_ids)))
        if product.product_type == "exhibitor":
            existing_ids = [item.id for item, selected in rows if selected.product_type == "exhibitor"]
            if existing_ids:
                await db.execute(delete(CartItem).where(CartItem.id.in_(existing_ids)))
        if product.delegate_package_rate_id:
            rate = await db.get(DelegatePackageRate, product.delegate_package_rate_id)
            package = await db.get(DelegatePackage, rate.delegate_package_id) if rate else None
            if not rate or not package or not rate.is_active or not package.is_active:
                raise ValidationException("PACKAGE_RATE_INACTIVE", "Tarif package tidak aktif")
            if payload.quantity != 1:
                raise ValidationException("PACKAGE_QUANTITY_INVALID", "Package delegate hanya dapat dipilih satu kali")
            if package.package_type == "additional":
                state, existing_order, _ = await StoreService._additional_purchase_state(db, user_id, event_id, package.id)
                # Before the initial registration exists, allow cart composition;
                # checkout will still require exactly one main package.
                if state not in {"available", "registration_required"}:
                    reference = f" pada order {existing_order.order_number}" if existing_order else ""
                    raise ConflictException("ADDITIONAL_PACKAGE_NOT_AVAILABLE", f"Additional package berstatus {state}{reference}")
            # Radio behavior for main packages and occupancy variants. Adding a
            # new rate replaces the previous selection in the same group.
            existing = (await db.execute(
                select(CartItem, Product, DelegatePackageRate, DelegatePackage)
                .join(Product, Product.id == CartItem.product_id)
                .join(DelegatePackageRate, DelegatePackageRate.id == Product.delegate_package_rate_id)
                .join(DelegatePackage, DelegatePackage.id == DelegatePackageRate.delegate_package_id)
                .where(CartItem.cart_id == cart.id)
            )).all()
            replace_ids = [item.id for item, _, _, selected_package in existing if selected_package.id == package.id or (package.package_type == "main" and selected_package.package_type == "main")]
            if replace_ids:
                await db.execute(delete(CartItem).where(CartItem.id.in_(replace_ids)))
        item = (await db.execute(select(CartItem).where(CartItem.cart_id == cart.id, CartItem.product_id == product.id))).scalar_one_or_none()
        quantity = (item.quantity if item else 0) + payload.quantity
        if product.max_quantity and quantity > product.max_quantity:
            raise ValidationException("PRODUCT_QUANTITY_LIMIT", "Jumlah product melebihi batas")
        if item:
            item.quantity = quantity
        else:
            db.add(CartItem(cart_id=cart.id, product_id=product.id, quantity=quantity))
        await db.commit()
        return await StoreService.get_cart(db, user_id, event_id)

    @staticmethod
    async def remove_item(db: AsyncSession, user_id, event_id, product_id):
        cart, _ = await StoreService.get_cart(db, user_id, event_id)
        result = await db.execute(delete(CartItem).where(CartItem.cart_id == cart.id, CartItem.product_id == product_id))
        if not result.rowcount:
            raise NotFoundException("CART_ITEM_NOT_FOUND", "Item tidak ditemukan di cart")
        await db.commit()
        return await StoreService.get_cart(db, user_id, event_id)

    @staticmethod
    async def checkout(db: AsyncSession, user_id, event_id, locale="en", terms_accepted: bool | None = None):
        # Match add_item's owner-before-cart lock order to avoid deadlocks.
        await db.execute(select(User.id).where(User.id == user_id).with_for_update())
        cart = (await db.execute(
            select(Cart)
            .where(Cart.user_id == user_id, Cart.event_id == event_id)
            .with_for_update()
        )).scalar_one_or_none()
        if not cart:
            raise ValidationException("EMPTY_CART", "Cart masih kosong")
        rows = (await db.execute(
            select(CartItem, Product)
            .join(Product, Product.id == CartItem.product_id)
            .where(CartItem.cart_id == cart.id)
            .order_by(Product.product_type, Product.name)
        )).all()
        if not rows:
            raise ValidationException("EMPTY_CART", "Cart masih kosong")
        event = await db.get(Event, event_id, with_for_update=True)
        is_hari_santri = bool(event and getattr(event, "slug", None) == "hari-santri-2026")
        if is_hari_santri and not terms_accepted:
            raise ValidationException("TERMS_ACCEPTANCE_REQUIRED", "Persetujuan syarat pendaftaran wajib diberikan")
        if is_hari_santri and (
            len(rows) != 1
            or rows[0][0].quantity != 1
            or rows[0][1].product_type != "hari_santri_package"
            or (rows[0][1].metadata_json or {}).get("activity_type") not in {"CYCLING", "FAMILY_WALK"}
        ):
            raise ValidationException("INVALID_HARI_SANTRI_PACKAGE", "Pilih satu paket Hari Santri untuk satu jenis kegiatan")
        if is_hari_santri:
            package_product = rows[0][1]
            selected_activity = (package_product.metadata_json or {}).get("activity_type")
            existing_activity = (await db.execute(
                select(OrderItem.metadata_json)
                .join(Order, Order.id == OrderItem.order_id)
                .where(
                    Order.user_id == user_id,
                    Order.event_id == event_id,
                    Order.status.in_({OrderStatus.DRAFT, OrderStatus.PENDING, OrderStatus.PARTIALLY_PAID, OrderStatus.PAID}),
                    OrderItem.product_type == "hari_santri_package",
                )
            )).scalars().all()
            if existing_activity and any((metadata or {}).get("activity_type") != selected_activity for metadata in existing_activity):
                raise ConflictException(
                    "HARI_SANTRI_ACTIVITY_ALREADY_SELECTED",
                    "Akun ini sudah memiliki paket Hari Santri untuk kegiatan yang berbeda",
                )
            if existing_activity:
                raise ConflictException(
                    "HARI_SANTRI_ORDER_ALREADY_EXISTS",
                    "Akun ini sudah memiliki order Hari Santri aktif atau lunas",
                )
            if package_product.max_quantity is not None:
                committed_quantity = await db.scalar(
                    select(func.coalesce(func.sum(OrderItem.quantity), 0))
                    .join(Order, Order.id == OrderItem.order_id)
                    .where(
                        OrderItem.product_id == package_product.id,
                        Order.status.in_({OrderStatus.DRAFT, OrderStatus.PENDING, OrderStatus.PARTIALLY_PAID, OrderStatus.PAID}),
                    )
                )
                if int(committed_quantity or 0) + rows[0][0].quantity > package_product.max_quantity:
                    raise ConflictException("PACKAGE_CAPACITY_EXCEEDED", "Kuota paket sudah habis")
        exhibitor_items = [(item, product) for item, product in rows if product.product_type == "exhibitor"]
        if exhibitor_items:
            await StoreService._require_exhibitor_available(db, user_id, event_id)
            if len(exhibitor_items) != 1 or exhibitor_items[0][0].quantity != 1:
                raise ValidationException("PACKAGE_QUANTITY_INVALID", "Pilih tepat satu exhibitor package")
        linked = [(item, product) for item, product in rows if product.delegate_package_rate_id]
        if linked:
            selections = []
            for item, product in linked:
                rate = await db.get(DelegatePackageRate, product.delegate_package_rate_id)
                package = await db.get(DelegatePackage, rate.delegate_package_id) if rate else None
                if not rate or not package or not rate.is_active or not package.is_active or item.quantity != 1:
                    raise ValidationException("INVALID_DELEGATE_SELECTION", "Pilihan package atau tarif tidak valid")
                selections.append((package, rate))
            mains = [(package, rate) for package, rate in selections if package.package_type == "main"]
            additional_only = not mains and all(package.package_type == "additional" for package, _ in selections)
            exhibitor_only = not mains and all(package.package_type == "exhibitor" for package, _ in selections)
            if len(mains) != 1 and not additional_only and not exhibitor_only:
                raise ValidationException("MAIN_PACKAGE_REQUIRED", "Tepat satu Main Package wajib dipilih")
            if len({package.id for package, _ in selections}) != len(selections):
                raise ValidationException("DUPLICATE_PACKAGE_SELECTION", "Satu package hanya boleh memiliki satu pilihan tarif")
        else:
            selections, mains, additional_only, exhibitor_only = [], [], False, False
        currencies = {product.currency for _, product in rows}
        if len(currencies) != 1:
            raise ValidationException("MIXED_CURRENCY", "Product dalam satu order harus memiliki currency yang sama")
        subtotal = sum((Decimal(str(product.price)) * item.quantity for item, product in rows), Decimal("0"))
        registration_id = None
        order_kind = OrderKind.HARI_SANTRI if is_hari_santri else OrderKind.MAIN_REGISTRATION if mains else OrderKind.EXHIBITOR
        if additional_only:
            registration = await StoreService._active_registration(db, user_id, event_id, lock=True)
            if not registration or not await StoreService._paid_main_order_exists(db, registration.id):
                raise ConflictException("ADDITIONAL_MAIN_PAYMENT_REQUIRED", "Main package harus lunas sebelum membeli additional package")
            for package, _ in selections:
                state, existing_order, _ = await StoreService._additional_purchase_state(db, user_id, event_id, package.id, registration)
                if state != "available":
                    reference = f" pada order {existing_order.order_number}" if existing_order else ""
                    raise ConflictException("ADDITIONAL_PACKAGE_NOT_AVAILABLE", f"Additional package berstatus {state}{reference}")
            registration_id = registration.id
            order_kind = OrderKind.ADDITIONAL
        order = Order(user_id=user_id, registration_id=registration_id, event_id=event_id, order_number=f"ORD-{uuid.uuid4().hex[:16].upper()}", order_kind=order_kind, subtotal=subtotal, discount_amount=0, tax_amount=0, service_fee=0, total_amount=subtotal, currency=currencies.pop(), status=OrderStatus.PENDING)
        if is_hari_santri:
            order.terms_accepted_at = datetime.now(timezone.utc)
            order.terms_version = "hari-santri-2026-v1"
        db.add(order)
        await db.flush()
        from app.modules.content_translations.service import translation_map
        product_translations = await translation_map(db, "product", [product.id for _, product in rows], locale)
        for item, product in rows:
            translation = product_translations.get(product.id)
            product_name, metadata = StoreService.localized_product_snapshot(product, translation)
            db.add(OrderItem(order_id=order.id, product_id=product.id, product_code=product.code, product_name=product_name, product_type=product.product_type, quantity=item.quantity, unit_price=product.price, currency=product.currency, line_total=Decimal(str(product.price)) * item.quantity, metadata_json=metadata))
        await db.execute(delete(CartItem).where(CartItem.cart_id == cart.id))
        await db.commit()
        await db.refresh(order)
        return order, len(rows)
