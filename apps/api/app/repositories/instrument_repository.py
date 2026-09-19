"""Persistence for the instrument dimension.

One row per (exchange, symbol) MARKET has ever tracked. Rows are never
deleted — an instrument leaving the active universe only flips
`is_active`; its historical candles remain valid and retained.
"""

from collections.abc import Sequence
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.market import Instrument as DomainInstrument
from app.models.instrument import Instrument

EXCHANGE = "bybit"


class InstrumentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_active(self) -> list[Instrument]:
        result = await self._session.execute(
            select(Instrument).where(Instrument.is_active.is_(True))
        )
        return list(result.scalars().all())

    async def list_all(self) -> list[Instrument]:
        result = await self._session.execute(select(Instrument))
        return list(result.scalars().all())

    async def reconcile_universe(
        self,
        discovered: Sequence[DomainInstrument],
        *,
        now: datetime,
        retention_days: int,
    ) -> tuple[list[Instrument], list[str]]:
        """Upsert the freshly-discovered universe against what's persisted.

        - A symbol seen for the first time is inserted with its history
          watermarks anchored to `now` (see `Instrument`'s docstring).
        - A previously-known symbol is marked active and `last_seen_at` is
          bumped; its watermarks are untouched, so reconciliation progress
          already made isn't lost.
        - A previously-active symbol absent from `discovered` this round is
          marked inactive. It is never deleted, and normal retention still
          applies to its candles.

        Returns `(active_instruments, newly_added_symbols)` — the latter is
        what the live collector needs to begin watching just-discovered
        instruments (M3 section 17) without resubscribing anything else.
        """
        target_start = now - timedelta(days=retention_days)
        discovered_by_symbol = {i.symbol: i for i in discovered}

        existing = {row.symbol: row for row in await self.list_all() if row.exchange == EXCHANGE}
        newly_added: list[str] = []

        for symbol, domain_instrument in discovered_by_symbol.items():
            row = existing.get(symbol)
            if row is None:
                self._session.add(
                    Instrument(
                        exchange=EXCHANGE,
                        symbol=symbol,
                        base_coin=domain_instrument.base_coin,
                        quote_coin=domain_instrument.quote_coin,
                        is_active=True,
                        first_seen_at=now,
                        last_seen_at=now,
                        history_target_start=target_start,
                        history_synced_from=now,
                        history_synced_through=now,
                    )
                )
                newly_added.append(symbol)
            else:
                row.is_active = True
                row.last_seen_at = now
                row.base_coin = domain_instrument.base_coin
                row.quote_coin = domain_instrument.quote_coin

        for symbol, row in existing.items():
            if symbol not in discovered_by_symbol and row.is_active:
                row.is_active = False

        await self._session.commit()
        return await self.list_active(), newly_added
