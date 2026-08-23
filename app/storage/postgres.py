from __future__ import annotations

import json
import os
import ssl
from typing import Iterable

import asyncpg

from app.core.models import Candle, Decision


class PostgresStore:
    def __init__(self, database_url: str):
        self.database_url = database_url
        self.pool: asyncpg.Pool | None = None

    def _ssl_option(self):
        """Return an explicit TLS policy for asyncpg.

        Supabase pooler certificates may fail Render's default certificate-chain
        verification in some regions. ``require`` keeps transport encrypted;
        operators can opt into strict verification with ``verify-full`` and a
        CA bundle supplied through ``PGSSLROOTCERT``.
        """
        if "supabase" not in self.database_url.lower():
            return None
        mode = os.getenv("DATABASE_SSL_MODE", "require").strip().lower()
        if mode == "require":
            return "require"
        if mode == "verify-full":
            cafile = os.getenv("PGSSLROOTCERT") or None
            return ssl.create_default_context(cafile=cafile)
        raise ValueError("DATABASE_SSL_MODE must be require or verify-full")

    async def connect(self) -> None:
        self.pool = await asyncpg.create_pool(
            self.database_url,
            ssl=self._ssl_option(),
            min_size=1,
            max_size=5,
            command_timeout=10,
        )

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

    async def recent_decisions(self, limit: int = 50, search: str = "") -> list[dict]:
        pool = self._require_pool()
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        search = search.strip().upper()
        async with pool.acquire() as connection:
            rows = await connection.fetch(
                """
                select decision_id, symbol, decision_time_ms, status, payload, decision_hash
                from decisions
                where ($2 = '' or upper(symbol) like '%' || $2 || '%' or upper(status) like '%' || $2 || '%')
                order by decision_time_ms desc
                limit $1
                """,
                limit,
                search,
            )
        return [dict(row) for row in rows]

    async def recent_paper_orders(
        self,
        view: str = "all",
        search: str = "",
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict]:
        pool = self._require_pool()
        if view not in {"all", "open", "closed"}:
            raise ValueError("view must be all, open, or closed")
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        if offset < 0:
            raise ValueError("offset must be non-negative")
        search = search.strip().upper()
        async with pool.acquire() as connection:
            rows = await connection.fetch(
                """
                with classified as (
                    select
                        o.order_id,
                        o.decision_id,
                        o.symbol,
                        o.side,
                        o.order_type,
                        o.status,
                        o.requested_quantity,
                        o.requested_price,
                        o.filled_quantity,
                        o.average_fill_price,
                        o.fees,
                        o.payload,
                        o.created_at,
                        o.updated_at,
                        case
                            when upper(o.status) in ('CLOSED','CANCELLED','REJECTED','EXPIRED') then 'closed'
                            else 'open'
                        end as lifecycle
                    from paper_orders o
                )
                select
                    c.order_id,
                    c.decision_id,
                    c.symbol,
                    c.side,
                    c.order_type,
                    c.status,
                    c.lifecycle,
                    c.requested_quantity,
                    c.requested_price,
                    c.filled_quantity,
                    c.average_fill_price,
                    c.fees,
                    c.payload,
                    c.created_at,
                    c.updated_at,
                    coalesce(
                        (
                            select jsonb_agg(
                                jsonb_build_object(
                                    'fill_id', f.fill_id,
                                    'quantity', f.quantity,
                                    'price', f.price,
                                    'fee', f.fee,
                                    'fill_time_ms', f.fill_time_ms,
                                    'payload', f.payload
                                ) order by f.fill_time_ms desc
                            )
                            from paper_fills f
                            where f.order_id = c.order_id
                        ),
                        '[]'::jsonb
                    ) as fills
                from classified c
                where ($1 = 'all' or c.lifecycle = $1)
                  and (
                      $2 = ''
                      or upper(c.order_id) like '%' || $2 || '%'
                      or upper(c.decision_id) like '%' || $2 || '%'
                      or upper(c.symbol) like '%' || $2 || '%'
                      or upper(c.status) like '%' || $2 || '%'
                  )
                order by c.updated_at desc, c.created_at desc
                limit $3 offset $4
                """,
                view,
                search,
                limit,
                offset,
            )
        return [dict(row) for row in rows]

    async def paper_order_counts(self) -> dict[str, int]:
        pool = self._require_pool()
        async with pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                select
                    count(*)::int as total,
                    count(*) filter (where upper(status) in ('CLOSED','CANCELLED','REJECTED','EXPIRED'))::int as closed,
                    count(*) filter (where upper(status) not in ('CLOSED','CANCELLED','REJECTED','EXPIRED'))::int as open
                from paper_orders
                """
            )
        return dict(row) if row else {"total": 0, "open": 0, "closed": 0}
