from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed application configuration, populated from environment variables."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"

    database_url: str = "postgresql+psycopg://truffler:truffler@localhost:5432/market_truffler"

    session_secret: str = Field(min_length=32)
    session_ttl_minutes: int = 60 * 24 * 7  # 7 days

    frontend_origin: str = "http://localhost:3000"
    allowed_origins: str = "http://localhost:3000"

    bybit_base_url: str = "https://api.bybit.com"
    bybit_timeout_seconds: float = 5.0
    bybit_ws_base_url: str = "wss://stream.bybit.com/v5/public/linear"

    # How much canonical 1m history MARKET tries to keep, end to end: the
    # bootstrap/recovery target window, and the retention horizon candles
    # (all timeframes) are pruned to. One rolling policy, not two.
    market_history_retention_days: int = 365
    # How many instruments the history reconciler works on concurrently.
    market_history_max_concurrent_instruments: int = 5
    # Rows requested per Bybit historical kline page (Bybit's documented max).
    market_history_page_size: int = 1000
    # Small delay between successive REST history requests, to stay well
    # under Bybit's documented 600-requests/5s per-IP limit with headroom
    # for the app's other REST traffic.
    market_history_request_delay_seconds: float = 0.2
    # How far behind "now" the live/catch-up frontier is allowed to trail
    # before the reconciler treats it as stale and fetches a REST catch-up
    # page for it, in minutes. Also used as the safety buffer against the
    # still-forming current candle.
    market_history_catchup_buffer_minutes: int = 2
    # How often the background reconciler re-discovers the Bybit universe
    # (to pick up new listings/delistings) and rolls candle partitions.
    market_universe_refresh_interval_seconds: float = 900.0

    # How long the frame synchronizer waits after each UTC minute boundary
    # before finalizing that minute's Market Frame, to let normal WebSocket
    # delivery jitter across hundreds of symbols/several connections settle.
    # 5s is comfortably above what M2/M3 real-network testing observed (live
    # candles typically land within ~1-2s of the boundary).
    market_frame_grace_period_seconds: float = 5.0

    @property
    def allowed_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.allowed_origins.split(",") if origin.strip()]

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # session_secret is required via env/.env
