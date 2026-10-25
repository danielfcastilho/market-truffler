from datetime import UTC, datetime
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

    # One rolling horizon for bootstrap/catch-up, coverage, and monthly
    # candle/member/Sniffer retention. Candle partitions still referenced by
    # retained members are preserved. Frame headers are not pruned.
    # Old per-instrument targets/frontiers are not rewritten by policy changes.
    market_history_retention_days: int = 30
    # How long Open Interest / Funding Rate observations are kept, in days.
    # Must cover REQUIRED_WARMUP + 1 day (checked at startup). Longer than
    # that strictly needed for live features so OINK CORP research datasets
    # can still replay a frame's OI/funding features well after the frame
    # (see docs/ARCHITECTURE.md, "Research data continuity").
    market_oi_funding_retention_days: int = 30
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
    # page for it, in minutes — gives the live WebSocket first chance to
    # deliver each just-closed 5m candle. Catch-up never reaches past the
    # last 5m boundary before `now - buffer`, so it never touches a candle
    # that is still forming.
    market_history_catchup_buffer_minutes: int = 2
    # How often the background reconciler re-discovers the Bybit universe
    # (to pick up new listings/delistings) and rolls candle partitions.
    market_universe_refresh_interval_seconds: float = 900.0

    # How long the frame synchronizer waits after each UTC 5-minute boundary
    # before finalizing that boundary's Market Frame, so each instrument's
    # just-closed 5m candle is *persisted* in time to be its frame anchor.
    # Bybit pushes them within ~1-2s, but persisting the whole ~800-candle
    # burst (upsert + aggregate re-derivation per candle) measured 8-11s
    # after the boundary; anything missing at finalization is anchored on
    # the previous candle, 5 minutes stale. A delay here costs nothing at a
    # 5-minute cadence.
    market_frame_grace_period_seconds: float = 15.0

    # Where app.scripts.generate_research_dataset writes its output inside
    # the container. Durable only because docker-compose.yml bind-mounts
    # this path to the host (research/oink_corp/data) — nothing else
    # written inside the api container survives a rebuild.
    research_data_dir: str = "/app/research_data"
    # OINK CORP research data continuity (app.research.continuity): daily
    # shards are generated for every completed UTC day from this instant on
    # (the first preserved dataset ends at 2026-10-12 14:50), with one pass
    # every `research_pass_interval_seconds`.
    research_shards_start: datetime = datetime(2026, 10, 12, 15, 0, tzinfo=UTC)
    research_pass_interval_seconds: float = 1800.0
    # Scheduled integrity verification of research datasets
    # (app.research.integrity): each is re-checked once per interval, at
    # most this many per worker pass.
    research_verification_interval_hours: float = 24.0
    research_verification_max_per_pass: int = 20
    # Isolated recovery test of the independent backup (app.research.backup):
    # once per interval, only when a destination is configured.
    research_recovery_test_interval_hours: float = 24.0
    # Independent backup of research records (app.research.backup): a path
    # inside the research container where the operator mounts storage that
    # is NOT the research data's disk. Empty = not configured (reported as
    # such, never assumed). See docs/ARCHITECTURE.md's runbook.
    research_backup_dir: str = ""
    # Committed OINK CORP artifacts the Research Explorer reads (read-only
    # mounts in docker-compose.yml): experiment specs, campaigns and their
    # knowledge ledgers.
    research_experiments_dir: str = "/app/research_experiments"
    research_campaigns_dir: str = "/app/research_campaigns"
    research_knowledge_dir: str = "/app/research_knowledge"
    research_ranking_dir: str = "/app/research_ranking"
    # Live Sniffer rankings (app.services.live_rankings): the ranking
    # candidates (files under `<research_ranking_dir>/candidates/`) applied
    # to the latest Sniffer frame. Experimental: manually specified, not
    # validated, not Champions. A missing or invalid candidate makes that
    # direction unavailable — nothing is substituted.
    live_ranking_long_candidate: str = "long-price-reversal-oi-v1"
    live_ranking_short_candidate: str = "short-price-reversal-oi-v1"
    # Experimental Truffle qualification (app.ranking.qualification): specs
    # under `<research_ranking_dir>/qualifications/`, each naming the
    # candidate it applies to. Missing or mismatched -> that direction's
    # qualification is unavailable, never substituted.
    live_truffle_long_qualification: str = "long-reversal-oi-truffle-v1"
    live_truffle_short_qualification: str = "short-reversal-oi-truffle-v1"

    @property
    def allowed_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.allowed_origins.split(",") if origin.strip()]

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # session_secret is required via env/.env
