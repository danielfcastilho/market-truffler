import logging

from fastapi import APIRouter, HTTPException, Response, status

from app.core.deps import CurrentUserDep, DbSessionDep, SettingsDep
from app.core.security import (
    SESSION_COOKIE_NAME,
    create_session_token,
    verify_password,
)
from app.repositories.user_repository import UserRepository
from app.schemas.user import LoginRequest, UserRead

router = APIRouter(tags=["auth"])
logger = logging.getLogger(__name__)


def _set_session_cookie(response: Response, token: str, settings: SettingsDep) -> None:
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=settings.is_production,
        samesite="lax",
        max_age=settings.session_ttl_minutes * 60,
        path="/",
    )


@router.post("/api/auth/login", response_model=UserRead)
async def login(
    payload: LoginRequest,
    response: Response,
    db: DbSessionDep,
    settings: SettingsDep,
) -> UserRead:
    user = await UserRepository(db).get_by_email(payload.email)
    if user is None or not user.is_active or not verify_password(
        payload.password, user.password_hash
    ):
        logger.info("login_failed", extra={"email": payload.email})
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password"
        )

    token = create_session_token(user.id, settings)
    _set_session_cookie(response, token, settings)
    logger.info("login_succeeded", extra={"user_id": str(user.id)})
    return UserRead.model_validate(user)


@router.post("/api/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")


@router.get("/api/me", response_model=UserRead)
async def me(current_user: CurrentUserDep) -> UserRead:
    return UserRead.model_validate(current_user)
