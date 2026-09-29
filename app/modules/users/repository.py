import uuid
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictException, NotFoundException
from app.modules.users.models import User


class UserRepository:
    @staticmethod
    async def create(session: AsyncSession, email: str, password_hash: str, country: str, phone: str, preferred_locale: str = "en", **location_codes) -> User:
        stmt = select(User).where(User.email == email.lower())
        result = await session.execute(stmt)
        if result.scalar_one_or_none():
            raise ConflictException(code="USER_EXISTS", message="Email sudah terdaftar", field="email")

        user = User(email=email.lower(), password_hash=password_hash, country=country or "Indonesia", phone=phone, preferred_locale=preferred_locale, **location_codes)
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user

    @staticmethod
    async def get_by_email(session: AsyncSession, email: str) -> User | None:
        stmt = select(User).where(User.email == email.lower())
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_id(session: AsyncSession, user_id: str | uuid.UUID) -> User | None:
        return await session.get(User, user_id)

    @staticmethod
    async def touch_last_login(session: AsyncSession, user: User) -> None:
        from datetime import datetime, timezone

        user.last_login_at = datetime.now(timezone.utc)
        session.add(user)
        await session.commit()
        await session.refresh(user)

    @staticmethod
    async def update_profile(session: AsyncSession, user: User, full_name: str | None, phone: str | None, preferred_locale: str | None = None) -> User:
        if full_name is not None:
            user.full_name = full_name
        if phone is not None:
            user.phone = phone
        if preferred_locale is not None:
            user.preferred_locale = preferred_locale
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user

    @staticmethod
    async def update_password(session: AsyncSession, user: User, password_hash: str) -> User:
        user.password_hash = password_hash
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user

    @staticmethod
    async def verify_email(session: AsyncSession, user: User) -> User:
        user.is_email_verified = True
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user
