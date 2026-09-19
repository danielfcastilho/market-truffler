import logging
import time

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import text

from app.core import PROCESS_STARTED_AT, __version__
from app.core.deps import CurrentUserDep, DbSessionDep, SettingsDep
from app.schemas.system import HealthResponse, ReadyResponse, SystemInfo

router = APIRouter(tags=["system"])
logger = logging.getLogger(__name__)


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Liveness probe: the process is up. No dependency checks."""
    return HealthResponse(status="ok")


@router.get("/ready", response_model=ReadyResponse)
async def ready(db: DbSessionDep) -> ReadyResponse:
    """Readiness probe: the app and its required dependencies (PostgreSQL) are usable."""
    try:
        await db.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 - readiness must report any failure
        logger.warning("readiness_check_failed", extra={"error": str(exc)})
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database not reachable",
        ) from exc
    return ReadyResponse(status="ok", database="ok")


@router.get("/api/system", response_model=SystemInfo)
async def system_info(
    db: DbSessionDep, settings: SettingsDep, current_user: CurrentUserDep
) -> SystemInfo:
    database_status = "ok"
    try:
        await db.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001
        database_status = "unreachable"

    return SystemInfo(
        environment=settings.app_env,
        version=__version__,
        database=database_status,
        authenticated_user=current_user.email,
        uptime_seconds=time.monotonic() - PROCESS_STARTED_AT,
    )
