from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from datetime import datetime, timedelta, timezone
import hashlib
import secrets

from app.core.exceptions import ValidationException
from app.core.security import create_access_token, create_refresh_token, hash_password, verify_password
from app.modules.users import schemas
from app.modules.users.repository import UserRepository
from app.modules.users.purchase_progress import purchase_status
from app.modules.regions.service import RegionService


class UserService:
    @staticmethod
    async def get_registration_detail(db: AsyncSession, user_id):
        from app.modules.iwbif.models import DelegatePackage, DelegateRegistrationDetail, ExhibitorRegistration
        from app.modules.participants.models import ParticipantProfile
        from app.modules.payments.models import Order, Payment
        from app.modules.payments.service import PaymentService
        from app.modules.registrations.models import Registration
        from app.modules.store.models import Cart, CartItem, OrderItem, Product

        user = await UserRepository.get_by_id(db, user_id)
        if not user:
            raise ValidationException("USER_NOT_FOUND", "User tidak ditemukan")

        participant = (await db.execute(select(ParticipantProfile).where(ParticipantProfile.user_id == user_id))).scalar_one_or_none()
        registrations = []
        if participant:
            delegate_rows = (await db.execute(
                select(Registration, DelegateRegistrationDetail, DelegatePackage)
                .outerjoin(DelegateRegistrationDetail, DelegateRegistrationDetail.registration_id == Registration.id)
                .outerjoin(DelegatePackage, DelegatePackage.id == DelegateRegistrationDetail.delegate_package_id)
                .where(Registration.participant_id == participant.id)
                .order_by(Registration.id.desc())
            )).all()
            for registration, detail, package in delegate_rows:
                registrations.append({
                    "id": registration.id,
                    "type": "delegate",
                    "event_id": registration.event_id,
                    "status": getattr(registration.status, "value", registration.status),
                    "registration_number": registration.registration_number,
                    "package": {"id": package.id, "code": package.code, "name": package.name, "amount": package.amount, "currency": package.currency} if package else None,
                })

            exhibitor_rows = (await db.execute(select(ExhibitorRegistration).where(ExhibitorRegistration.participant_id == participant.id).order_by(ExhibitorRegistration.id.desc()))).scalars().all()
            registrations.extend({
                "id": row.id,
                "type": "exhibitor",
                "event_id": row.event_id,
                "status": row.status,
                "registration_number": None,
                "package": None,
            } for row in exhibitor_rows)

        orders = (await db.execute(select(Order).where(Order.user_id == user_id).order_by(Order.created_at.desc()))).scalars().all()
        order_data = []
        for order in orders:
            items = (await db.execute(select(OrderItem).where(OrderItem.order_id == order.id))).scalars().all()
            payment = (await db.execute(select(Payment).where(Payment.order_id == order.id, Payment.deleted_at.is_(None)).order_by(Payment.created_at.desc()))).scalars().first()
            paid_amount, remaining_amount = await PaymentService._payment_progress(db, order)
            order_data.append({
                "id": order.id,
                "order_number": order.order_number,
                "registration_id": order.registration_id,
                "event_id": order.event_id,
                "allowed_actions": order.allowed_actions,
                "paid_amount": float(paid_amount),
                "remaining_amount": float(remaining_amount),
                "is_payment_complete": order.status == "paid" and remaining_amount == 0,
                "status": order.status,
                "subtotal": order.subtotal,
                "total_amount": order.total_amount,
                "currency": order.currency,
                "created_at": order.created_at,
                "payment": {"id": payment.id, "status": payment.transaction_status, "provider": payment.provider, "paid_at": payment.paid_at} if payment else None,
                "items": [{"product_id": item.product_id, "code": item.product_code, "name": item.product_name, "type": item.product_type, "quantity": item.quantity, "unit_price": item.unit_price, "line_total": item.line_total, "currency": item.currency} for item in items],
            })

        cart_rows = (await db.execute(
            select(CartItem, Product)
            .join(Cart, Cart.id == CartItem.cart_id)
            .join(Product, Product.id == CartItem.product_id)
            .where(Cart.user_id == user_id, Product.is_active.is_(True))
        )).all()
        selected_products = [
            {"id": product.id, "code": product.code, "name": product.name, "type": product.product_type, "quantity": item.quantity, "price": product.price, "currency": product.currency, "source": "cart"}
            for item, product in cart_rows
            if product.product_type in {"delegate", "exhibitor"}
        ]
        for order in order_data:
            if order["status"] != "paid" and "continue_payment" not in order["allowed_actions"]:
                continue
            for item in order["items"]:
                if item["type"] in {"delegate", "exhibitor"}:
                    selected_products.append({**item, "source": "order", "order_id": order["id"], "order_status": order["status"], "is_payment_complete": order["is_payment_complete"], "payment_status": order["payment"]["status"] if order["payment"] else None})

        selected_types = sorted(
            {row["type"] for row in registrations}
            | {product["type"] for product in selected_products}
        )
        delegate_rows = [row for row in registrations if row["type"] == "delegate"]
        exhibitor_rows = [row for row in registrations if row["type"] == "exhibitor"]
        complete_delegate_statuses = {"submitted", "under_verification", "verified", "payment_pending", "paid", "confirmed"}
        delegate_status = "belum_terdaftar"
        exhibitor_status = "belum_terdaftar"
        if delegate_rows:
            delegate_status = "lengkap" if any(row["status"] in complete_delegate_statuses for row in delegate_rows) else "belum_lengkap"
        if exhibitor_rows:
            exhibitor_status = "lengkap" if any(row["status"] in {"submitted", "paid", "confirmed"} for row in exhibitor_rows) else "belum_lengkap"
        tracking = {}
        for product_type, rows in (("delegate", delegate_rows), ("exhibitor", exhibitor_rows)):
            products = [product for product in selected_products if product["type"] == product_type]
            registered = any(row["type"] == product_type for row in registrations)
            complete = any(
                row["status"] in (complete_delegate_statuses if product_type == "delegate" else {"submitted", "paid", "confirmed"})
                for row in rows
            )
            state = purchase_status(products, registered=registered, complete=complete)
            tracking[product_type] = {"status": state, "products": products, "profile_required": state == "paid_profile_incomplete"}
        states = {item["status"] for item in tracking.values()}
        if "payment_pending" in states:
            effective_status = "payment_pending"
        elif "selected" in states:
            effective_status = "package_selected"
        elif states & {"completed", "paid_profile_incomplete"}:
            effective_status = "paid"
        else:
            effective_status = "account_created"
        return {
            "user": schemas.UserRead.model_validate(user),
            "registration_status": effective_status,
            "delegate_status": delegate_status,
            "exhibitor_status": exhibitor_status,
            "purchase_tracking": tracking,
            "selected_types": selected_types,
            "profile": schemas.UserProfileSnapshot.model_validate(participant) if participant else None,
            "registrations": registrations,
            "orders": order_data,
        }
    @staticmethod
    async def register(db: AsyncSession, payload: schemas.UserCreate) -> tuple[schemas.UserRead, str, str]:
        location_codes = (payload.province_code, payload.regency_code, payload.district_code, payload.village_code)
        if any(location_codes):
            if not all(location_codes):
                raise ValidationException("REGION_REQUIRED", "Provinsi sampai desa wajib dipilih")
            await RegionService.validate_chain(db, *location_codes)
        elif not payload.country:
            raise ValidationException("REGION_REQUIRED", "Wilayah tempat tinggal wajib dipilih")
        password_hash = hash_password(payload.password)
        user = await UserRepository.create(
            session=db,
            email=payload.email,
            password_hash=password_hash,
            country=payload.country or "Indonesia",
            phone=payload.phone,
            preferred_locale=payload.preferred_locale,
            province_code=payload.province_code,
            regency_code=payload.regency_code,
            district_code=payload.district_code,
            village_code=payload.village_code,
        )
        access_token = create_access_token(str(user.id))
        refresh_token = create_refresh_token(str(user.id))
        return schemas.UserRead.model_validate(user), access_token, refresh_token

    @staticmethod
    async def login(db: AsyncSession, payload: schemas.UserLogin) -> tuple[schemas.UserRead, str, str]:
        user = await UserRepository.get_by_email(db, payload.email)
        if not user:
            raise ValidationException(code="INVALID_CREDENTIAL", message="Email atau password salah")
        if not verify_password(payload.password, user.password_hash):
            raise ValidationException(code="INVALID_CREDENTIAL", message="Email atau password salah")
        await UserRepository.touch_last_login(db, user)
        access_token = create_access_token(str(user.id))
        refresh_token = create_refresh_token(str(user.id))
        return schemas.UserRead.model_validate(user), access_token, refresh_token

    @staticmethod
    async def refresh(refresh_token: str) -> tuple[str, str]:
        try:
            from app.core.security import decode_token

            payload = decode_token(refresh_token)
            if payload.get("type") != "refresh":
                raise ValidationException(code="INVALID_TOKEN", message="Token bukan refresh token")
            user_id = payload.get("sub")
            access_token = create_access_token(user_id)
            refresh = create_refresh_token(user_id)
            return access_token, refresh
        except Exception as exc:
            raise ValidationException(code="INVALID_TOKEN", message="Refresh token tidak valid") from exc

    @staticmethod
    async def update_profile(db: AsyncSession, user, payload: schemas.UserUpdate) -> schemas.UserRead:
        user = await UserRepository.update_profile(
            session=db,
            user=user,
            full_name=payload.full_name,
            phone=payload.phone,
            preferred_locale=payload.preferred_locale,
        )
        return schemas.UserRead.model_validate(user)

    @staticmethod
    async def change_password(db: AsyncSession, user, payload: schemas.ChangePassword) -> None:
        if not verify_password(payload.current_password, user.password_hash):
            raise ValidationException(code="INVALID_CREDENTIAL", message="Password saat ini tidak sesuai")
        if payload.new_password != payload.confirm_password:
            raise ValidationException(code="PASSWORD_MISMATCH", message="Konfirmasi password tidak cocok")
        if payload.new_password == payload.current_password:
            raise ValidationException(code="WEAK_PASSWORD", message="Password baru harus berbeda dari password lama")

        hashed_password = hash_password(payload.new_password)
        await UserRepository.update_password(session=db, user=user, password_hash=hashed_password)
        return None

    @staticmethod
    async def forgot_password(db: AsyncSession, email: str) -> tuple[str, str] | None:
        from app.core.config import get_settings
        from app.modules.users.models import PasswordResetToken

        user = await UserRepository.get_by_email(db, email)
        if not user:
            # Tetap gunakan pesan yang sama untuk menghindari kebocoran akun.
            return None
        now = datetime.now(timezone.utc)
        await db.execute(
            update(PasswordResetToken)
            .where(
                PasswordResetToken.user_id == user.id,
                PasswordResetToken.used_at.is_(None),
            )
            .values(used_at=now)
        )
        reset_token = secrets.token_urlsafe(32)
        db.add(PasswordResetToken(
            user_id=user.id,
            token_hash=hashlib.sha256(reset_token.encode("utf-8")).hexdigest(),
            expires_at=now + timedelta(minutes=get_settings().PASSWORD_RESET_EXPIRE_MINUTES),
        ))
        await db.commit()
        return user.email, reset_token

    @staticmethod
    async def reset_password(db: AsyncSession, token: str, password: str, confirm_password: str) -> bool:
        from app.modules.users.models import PasswordResetToken

        if password != confirm_password:
            raise ValidationException(code="PASSWORD_MISMATCH", message="Konfirmasi password tidak cocok")
        try:
            now = datetime.now(timezone.utc)
            token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
            reset_token = (await db.execute(
                select(PasswordResetToken)
                .where(PasswordResetToken.token_hash == token_hash)
                .with_for_update()
            )).scalar_one_or_none()
            if not reset_token or reset_token.used_at is not None:
                raise ValidationException(code="INVALID_TOKEN", message="Token tidak valid")
            expires_at = reset_token.expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if expires_at <= now:
                raise ValidationException(code="EXPIRED_TOKEN", message="Token reset password sudah kedaluwarsa")
            user = await UserRepository.get_by_id(db, reset_token.user_id)
            if not user:
                raise ValidationException(code="INVALID_TOKEN", message="Token tidak valid")
            user.password_hash = hash_password(password)
            reset_token.used_at = now
            await db.execute(
                update(PasswordResetToken)
                .where(
                    PasswordResetToken.user_id == user.id,
                    PasswordResetToken.used_at.is_(None),
                )
                .values(used_at=now)
            )
            await db.commit()
            return True
        except ValidationException:
            raise
        except Exception as exc:
            raise ValidationException(code="INVALID_TOKEN", message="Token tidak valid") from exc

    @staticmethod
    async def verify_email(db: AsyncSession, token: str) -> bool:
        try:
            from app.core.security import decode_token

            payload = decode_token(token)
            if payload.get("type") != "email_verification":
                raise ValidationException(code="INVALID_TOKEN", message="Token tidak valid")
            user_id = payload.get("sub")
            if not user_id:
                raise ValidationException(code="INVALID_TOKEN", message="Token tidak valid")
            user = await UserRepository.get_by_id(db, user_id)
            if not user:
                raise ValidationException(code="INVALID_TOKEN", message="Token tidak valid")
            if user.is_email_verified:
                return True
            await UserRepository.verify_email(session=db, user=user)
            return True
        except ValidationException:
            raise
        except Exception as exc:
            raise ValidationException(code="INVALID_TOKEN", message="Token tidak valid") from exc
