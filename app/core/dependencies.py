from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.database import get_db
from app.core.security import TokenDecodeError, TokenExpiredError, decode_token
from app.modules.users.repository import UserRepository
from app.modules.users.models import User
from app.modules.users.schemas import UserRead


bearer_scheme = HTTPBearer(auto_error=False)


def decode_bearer_token(token: str) -> dict:
    try:
        return decode_token(token)
    except TokenExpiredError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Bearer token expired",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    except TokenDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


async def get_db_session() -> AsyncSession:
    async for session in get_db():
        yield session


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db_session),
) -> User:
    if not credentials or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")
    token = credentials.credentials.strip()
    payload = decode_bearer_token(token)
    if payload.get("type") != "access":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token type")
    user = await UserRepository.get_by_id(db, payload.get("sub"))
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid user")
    return user


async def get_current_user_payload(authorization: str | None = Header(default=None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")
    token = authorization.removeprefix("Bearer ").strip()
    return decode_bearer_token(token)


async def require_admin(current_user: User = Depends(get_current_user)) -> User:
    if current_user.role not in {"admin", "organizer"}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Organizer role required")
    return current_user


async def require_checkin_staff(current_user: User = Depends(get_current_user)) -> User:
    if current_user.status != "active" or current_user.role not in {"admin", "organizer", "checkin_staff"}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Check-in staff role required")
    return current_user
