from typing import Annotated

from fastapi import Cookie, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.security import SESSION_COOKIE_NAME, InvalidSessionToken, read_session_token
from app.db.session import get_db
from app.integrations.bybit.client import BybitClient
from app.models.user import User
from app.repositories.user_repository import UserRepository
from app.services.market_universe import MarketUniverseService

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


def get_bybit_client(settings: SettingsDep) -> BybitClient:
    return BybitClient(
        base_url=settings.bybit_base_url, timeout_seconds=settings.bybit_timeout_seconds
    )


BybitClientDep = Annotated[BybitClient, Depends(get_bybit_client)]


def get_market_universe_service(client: BybitClientDep) -> MarketUniverseService:
    return MarketUniverseService(client)


MarketUniverseServiceDep = Annotated[MarketUniverseService, Depends(get_market_universe_service)]
