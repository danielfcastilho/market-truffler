from typing import Annotated

from fastapi import Cookie, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.security import SESSION_COOKIE_NAME, InvalidSessionToken, read_session_token
from app.db.session import get_db
from app.models.user import User
from app.repositories.user_repository import UserRepository

SettingsDep = Annotated[Settings, Depends(get_settings)]
DbSessionDep = Annotated[AsyncSession, Depends(get_db)]


async def get_current_user(
    settings: SettingsDep,
    db: DbSessionDep,
    session_token: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None,
) -> User:
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Cookie"},
    )
    if session_token is None:
        raise unauthorized

    try:
        user_id = read_session_token(session_token, settings)
    except InvalidSessionToken as exc:
        raise unauthorized from exc

    user = await UserRepository(db).get_by_id(user_id)
    if user is None or not user.is_active:
        raise unauthorized
    return user


CurrentUserDep = Annotated[User, Depends(get_current_user)]
