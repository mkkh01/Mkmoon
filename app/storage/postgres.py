from __future__ import annotations

import json
from typing import Iterable

import asyncpg

from app.core.models import Candle, Decision


class PostgresStore:
    def __init__(self, database_url: str):
        self.database_url = database_url
        self.pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        self.pool = await asyncpg.create_pool(self.database_url, min_size=1, max_size=5, command_timeout=10)

    async def close(self) -> None:
        if self.pool:
            await self.pool.close()
            self.pool = None

    def _require_pool(self) -> asyncpg.Pool:
        if self.pool is None:
            raise RuntimeError("PostgresStore is not connected")
        return self.pool

    async def save_candles(self, candles: Iterable[Candle]) -> None:
        pool = self._require_pool()
        rows = [
            (c.symbol, c.timeframe, c.open_time_ms, c.close_time_ms, c.open, c.high, c.low, c.close, c.volume, c.source_id)
            for c in candles
        ]
        if not rows:
            return
        async with pool.acquire() as connection:
            await connection.executemany(
                """
                insert into candles(symbol, timeframe, open_time_ms, close_time_ms, open_price, high_price, low_price, close_price, volume, source_id)
                values($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
                on conflict(symbol, timeframe, open_time_ms) do nothing
                """,
                rows,
            )

    async def save_decision(self, decision: Decision) -> None:
        pool = self._require_pool()
        payload = decision.model_dump(mode="json")
        async with pool.acquire() as connection:
            await connection.execute(
                """
                insert into decisions(decision_id, symbol, decision_time_ms, data_cutoff_ms, status, payload, decision_hash, code_version, feature_version, config_version)
                values($1,$2,$3,$4,$5,$6::jsonb,$7,$8,$9,$10)
                on conflict(decision_id) do nothing
                """,
                decision.decision_id,
                decision.symbol,
                decision.decision_time_ms,
                decision.data_cutoff_ms,
                decision.status.value,
                json.dumps(payload),
                decision.decision_hash,
                decision.lineage.get("code_version", "unknown"),
                decision.lineage.get("feature_version", "unknown"),
                decision.lineage.get("config_version", "unknown"),
            )

    async def recent_decisions(self, limit: int = 50) -> list[dict]:
        pool = self._require_pool()
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        async with pool.acquire() as connection:
            rows = await connection.fetch(
                "select decision_id, symbol, decision_time_ms, status, payload, decision_hash from decisions order by decision_time_ms desc limit $1",
                limit,
            )
        return [dict(row) for row in rows]
