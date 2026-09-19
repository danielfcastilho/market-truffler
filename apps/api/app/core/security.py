from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt
from passlib.context import CryptContext

from app.core.config import Settings

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

_JWT_ALGORITHM = "HS256"

SESSION_COOKIE_NAME = "truffler_session"


def hash_password(plain_password: str) -> str:
    return _pwd_context.hash(plain_password)


def verify_password(plain_password: str, password_hash: str) -> bool:
    return _pwd_context.verify(plain_password, password_hash)


def create_session_token(user_id: UUID, settings: Settings) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "iat": now,
        "exp": now + timedelta(minutes=settings.session_ttl_minutes),
    }
    return jwt.encode(payload, settings.session_secret, algorithm=_JWT_ALGORITHM)


class InvalidSessionToken(Exception):
    pass


def read_session_token(token: str, settings: Settings) -> UUID:
    try:
        payload = jwt.decode(token, settings.session_secret, algorithms=[_JWT_ALGORITHM])
        return UUID(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError) as exc:
        raise InvalidSessionToken from exc
