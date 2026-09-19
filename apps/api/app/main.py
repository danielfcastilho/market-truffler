import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core import __version__
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.session import get_session_factory
from app.integrations.bybit.client import BybitClient
from app.routers import auth, market, sniffer, system
from app.services.frame_synchronizer import FrameSynchronizer
from app.services.history_reconciler import HistoryReconciler
from app.services.live_candle_sink import PersistingCandleSink
from app.services.market_collector import MarketCollector
from app.services.market_universe import MarketUniverseService
from app.services.sniffer import Sniffer

settings = get_settings()
configure_logging(settings.log_level)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("startup", extra={"environment": settings.app_env, "version": __version__})

    session_factory = get_session_factory()

    bybit_client = BybitClient(
        base_url=settings.bybit_base_url, timeout_seconds=settings.bybit_timeout_seconds
    )
    market_universe_service = MarketUniverseService(bybit_client)

    live_sink = PersistingCandleSink(session_factory)
    market_collector = MarketCollector(
        market_universe_service, settings.bybit_ws_base_url, live_sink
    )
    app.state.market_collector = market_collector

    history_reconciler = HistoryReconciler(
        session_factory,
        bybit_client,
        market_universe_service,
        retention_days=settings.market_history_retention_days,
        page_size=settings.market_history_page_size,
        max_concurrent_instruments=settings.market_history_max_concurrent_instruments,
        request_delay_seconds=settings.market_history_request_delay_seconds,
        catchup_buffer_minutes=settings.market_history_catchup_buffer_minutes,
        universe_refresh_interval_seconds=settings.market_universe_refresh_interval_seconds,
        on_new_symbols=market_collector.add_symbols,
    )
    app.state.history_reconciler = history_reconciler

    # Sniffer has no lifecycle of its own — it's invoked reactively via the
    # hook below, once per finalized frame, never polling and never
    # triggered by a request. A Sniffer failure is isolated inside
    # FrameSynchronizer itself (see its `on_frame_finalized` handling), so
    # it can never take MARKET down.
    sniffer_service = Sniffer(session_factory)
    app.state.sniffer = sniffer_service

    frame_synchronizer = FrameSynchronizer(
        session_factory,
        grace_period_seconds=settings.market_frame_grace_period_seconds,
        on_frame_finalized=sniffer_service.on_frame_finalized,
    )
    app.state.frame_synchronizer = frame_synchronizer

    # The reconciler's first universe pass populates `instruments` (which the
    # live sink and the collector's own discovery both then rely on), so it
    # must start before the collector. The frame synchronizer only reads
    # already-persisted candles, so its start order relative to the other
    # two doesn't matter beyond both being up before its first minute tick.
    await history_reconciler.start()
    await market_collector.start()
    await frame_synchronizer.start()

    yield

    await frame_synchronizer.stop()
    await market_collector.stop()
    await history_reconciler.stop()
    logger.info("shutdown")


app = FastAPI(title="Market Truffler API", version=__version__, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
        headers=exc.headers,
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled_exception", extra={"path": request.url.path})
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


app.include_router(system.router)
app.include_router(auth.router)
app.include_router(market.router)
app.include_router(sniffer.router)
