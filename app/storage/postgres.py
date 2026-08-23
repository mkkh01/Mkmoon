from __future__ import annotations

import json
import os
import ssl
import time
from decimal import Decimal
from typing import Iterable

import asyncpg

from app.core.models import Candle, Decision


def _decode_jsonb(value):
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8")
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _decode_row_json(row: dict, fields: tuple[str, ...]) -> dict:
    result = dict(row)
    for field in fields:
        if field in result:
            result[field] = _decode_jsonb(result[field])
    return result


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
        return [_decode_row_json(dict(row), ("payload",)) for row in rows]

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

    async def decision_count(self, search: str = "") -> int:
        pool = self._require_pool()
        search = search.strip().upper()
        async with pool.acquire() as connection:
            value = await connection.fetchval(
                """
                select count(*)::int
                from decisions
                where ($1 = '' or upper(symbol) like '%' || $1 || '%' or upper(status) like '%' || $1 || '%')
                """,
                search,
            )
        return int(value or 0)

    async def observed_realized_r(self, limit: int = 5000) -> list[Decimal]:
        pool = self._require_pool()
        if not 1 <= limit <= 10000:
            raise ValueError("limit must be between 1 and 10000")
        async with pool.acquire() as connection:
            rows = await connection.fetch(
                """
                select realized_pnl, payload->>'risk_cash' as risk_cash
                from paper_positions
                where state='CLOSED' and realized_pnl is not null
                order by closed_at_ms desc
                limit $1
                """,
                limit,
            )
        result: list[Decimal] = []
        for row in rows:
            risk_cash = Decimal(str(row["risk_cash"] or "0"))
            if risk_cash > 0:
                result.append(Decimal(str(row["realized_pnl"] or "0")) / risk_cash)
        return result

    async def active_paper_position(self, symbol: str) -> dict | None:
        pool = self._require_pool()
        async with pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                select position_id, account_id, order_id, decision_id, symbol, state,
                       quantity, entry_price, stop_price, target_price, entry_fee,
                       exit_price, exit_fee, realized_pnl, opened_at_ms, closed_at_ms, payload
                from paper_positions
                where symbol=$1 and state in ('ENTERED','ACTIVE','PROTECTED','EXITING')
                order by opened_at_ms desc
                limit 1
                """,
                symbol,
            )
        return dict(row) if row else None

    async def create_paper_trade(self, decision: Decision, entry, data_source: str) -> dict:
        """Atomically reserve risk, create/fill a Paper BUY, and open one position."""
        pool = self._require_pool()
        if decision.status.value != "ENTER" or decision.risk is None:
            return {"status": "SKIPPED", "reason": "DECISION_NOT_ENTER"}
        order_id = f"paper-order:{decision.decision_id}"
        reservation_id = f"paper-reservation:{decision.decision_id}"
        position_id = f"paper-position:{decision.decision_id}"
        fill_id = f"paper-fill:entry:{decision.decision_id}"
        now_ms = int(time.time() * 1000)
        quantity = entry.quantity
        fill_price = entry.fill_price
        notional = quantity * fill_price
        total_entry_cash = notional + entry.fee
        payload = {
            "mode": "paper",
            "data_source": data_source,
            "risk_cash": str(decision.risk.risk_cash),
            "notional": str(notional),
            "entry_decision_time_ms": decision.decision_time_ms,
        }
        async with pool.acquire() as connection:
            async with connection.transaction():
                existing = await connection.fetchrow(
                    "select order_id, status from paper_orders where decision_id=$1 limit 1",
                    decision.decision_id,
                )
                if existing:
                    return {"status": "DUPLICATE", "order_id": existing["order_id"], "order_status": existing["status"]}
                active = await connection.fetchval(
                    """
                    select position_id from paper_positions
                    where symbol=$1 and state in ('ENTERED','ACTIVE','PROTECTED','EXITING')
                    limit 1
                    """,
                    decision.symbol,
                )
                if active:
                    return {"status": "SKIPPED", "reason": "ACTIVE_POSITION_EXISTS", "position_id": active}
                account = await connection.fetchrow(
                    "select cash_balance, reserved_cash from paper_accounts where account_id='paper:default' for update"
                )
                if account is None:
                    raise RuntimeError("paper account is not initialized; apply migration 0006")
                if Decimal(str(account["cash_balance"])) < total_entry_cash:
                    await connection.execute(
                        """
                        insert into paper_orders(order_id, decision_id, symbol, side, order_type, status,
                            requested_quantity, requested_price, payload)
                        values($1,$2,$3,'BUY','MARKET_PAPER','REJECTED',$4,$5,$6::jsonb)
                        """,
                        order_id, decision.decision_id, decision.symbol, quantity, fill_price,
                        json.dumps({**payload, "reason": "INSUFFICIENT_PAPER_CASH"}),
                    )
                    return {"status": "REJECTED", "reason": "INSUFFICIENT_PAPER_CASH", "order_id": order_id}
                await connection.execute(
                    """
                    insert into risk_reservations(reservation_id, decision_id, symbol, risk_cash, status)
                    values($1,$2,$3,$4,'CONSUMED')
                    """,
                    reservation_id, decision.decision_id, decision.symbol, decision.risk.risk_cash,
                )
                await connection.execute(
                    """
                    insert into paper_orders(order_id, decision_id, symbol, side, order_type, status,
                        requested_quantity, requested_price, filled_quantity, average_fill_price, fees, payload)
                    values($1,$2,$3,'BUY','MARKET_PAPER','FILLED',$4,$5,$4,$5,$6,$7::jsonb)
                    """,
                    order_id, decision.decision_id, decision.symbol, quantity, fill_price, entry.fee,
                    json.dumps(payload),
                )
                await connection.execute(
                    """
                    insert into paper_fills(fill_id, order_id, symbol, quantity, price, fee, fill_time_ms, payload)
                    values($1,$2,$3,$4,$5,$6,$7,$8::jsonb)
                    """,
                    fill_id, order_id, decision.symbol, quantity, fill_price, entry.fee, entry.entry_time_ms,
                    json.dumps({"side": "BUY", "fill_type": "ENTRY", **payload}),
                )
                await connection.execute(
                    """
                    insert into paper_positions(
                        position_id, account_id, order_id, decision_id, symbol, state, quantity,
                        entry_price, stop_price, target_price, entry_fee, opened_at_ms, payload)
                    values($1,'paper:default',$2,$3,$4,'ACTIVE',$5,$6,$7,$8,$9,$10,$11::jsonb)
                    """,
                    position_id, order_id, decision.decision_id, decision.symbol, quantity, fill_price,
                    decision.stop_price, decision.target_price, entry.fee, entry.entry_time_ms,
                    json.dumps(payload),
                )
                await connection.execute(
                    """
                    insert into signal_lifecycle_events(signal_id, sequence_id, state, event_type, event_time_ms, payload)
                    values($1,1,'ENTERED','FIRST_FILL',$2,$3::jsonb),
                          ($1,2,'ACTIVE','ACTIVATE',$2,$3::jsonb)
                    on conflict(signal_id, sequence_id) do nothing
                    """,
                    decision.decision_id, entry.entry_time_ms, json.dumps(payload),
                )
                new_cash = Decimal(str(account["cash_balance"])) - total_entry_cash
                new_reserved = Decimal(str(account["reserved_cash"])) + decision.risk.risk_cash
                active_notional = await connection.fetchval(
                    """
                    select coalesce(sum(quantity * entry_price), 0)
                    from paper_positions
                    where state in ('ENTERED','ACTIVE','PROTECTED','EXITING')
                    """
                )
                await connection.execute(
                    """
                    update paper_accounts
                    set cash_balance=$1, reserved_cash=$2, equity=$3, updated_at_ms=$4
                    where account_id='paper:default'
                    """,
                    new_cash, new_reserved, new_cash + Decimal(str(active_notional or "0")), now_ms,
                )
        return {"status": "FILLED", "order_id": order_id, "position_id": position_id, "fill_id": fill_id}

    async def close_paper_position(self, position: dict, fill, candle_time_ms: int, exit_payload: dict) -> dict:
        """Atomically close an active Paper position after a closed-candle exit decision."""
        pool = self._require_pool()
        if fill.status != "CLOSED" or fill.fill_price is None:
            return {"status": fill.status}
        position_id = str(position["position_id"])
        exit_fill_id = f"paper-fill:exit:{position_id}:{candle_time_ms}"
        exit_price = fill.fill_price
        quantity = Decimal(str(position["quantity"]))
        entry_price = Decimal(str(position["entry_price"]))
        entry_fee = Decimal(str(position["entry_fee"]))
        exit_fee = fill.fee
        net_pnl = (exit_price - entry_price) * quantity - entry_fee - exit_fee
        risk_cash = Decimal(str((position.get("payload") or {}).get("risk_cash", "0")))
        async with pool.acquire() as connection:
            async with connection.transaction():
                current = await connection.fetchrow(
                    "select state from paper_positions where position_id=$1 for update", position_id
                )
                if current is None or current["state"] == "CLOSED":
                    return {"status": "DUPLICATE_OR_CLOSED", "position_id": position_id}
                account = await connection.fetchrow(
                    "select cash_balance, reserved_cash, realized_pnl from paper_accounts where account_id='paper:default' for update"
                )
                if account is None:
                    raise RuntimeError("paper account is not initialized")
                await connection.execute(
                    """
                    insert into paper_fills(fill_id, order_id, symbol, quantity, price, fee, fill_time_ms, payload)
                    values($1,$2,$3,$4,$5,$6,$7,$8::jsonb)
                    on conflict(fill_id) do nothing
                    """,
                    exit_fill_id, position["order_id"], position["symbol"], quantity, exit_price, exit_fee,
                    candle_time_ms, json.dumps({"side": "SELL", "fill_type": "EXIT", **exit_payload}),
                )
                await connection.execute(
                    """
                    update paper_orders set status='CLOSED', updated_at=now(), fees=fees+$2
                    where order_id=$1
                    """,
                    position["order_id"], exit_fee,
                )
                await connection.execute(
                    """
                    update paper_positions
                    set state='CLOSED', exit_price=$2, exit_fee=$3, realized_pnl=$4,
                        closed_at_ms=$5, updated_at=now(), payload=payload || $6::jsonb
                    where position_id=$1
                    """,
                    position_id, exit_price, exit_fee, net_pnl, candle_time_ms,
                    json.dumps({"exit_reason": fill.exit_reason, "realized_r": str(net_pnl / risk_cash) if risk_cash > 0 else None}),
                )
                await connection.execute(
                    "update risk_reservations set status='RELEASED', updated_at=now() where decision_id=$1 and status='CONSUMED'",
                    position["decision_id"],
                )
                proceeds = exit_price * quantity - exit_fee
                new_cash = Decimal(str(account["cash_balance"])) + proceeds
                new_reserved = max(Decimal("0"), Decimal(str(account["reserved_cash"])) - risk_cash)
                realized = Decimal(str(account["realized_pnl"])) + net_pnl
                active_notional = await connection.fetchval(
                    """
                    select coalesce(sum(quantity * entry_price), 0)
                    from paper_positions
                    where state in ('ENTERED','ACTIVE','PROTECTED','EXITING')
                    """
                )
                await connection.execute(
                    """
                    update paper_accounts set cash_balance=$1, reserved_cash=$2, equity=$3,
                        realized_pnl=$4, updated_at_ms=$5 where account_id='paper:default'
                    """,
                    new_cash, new_reserved, new_cash + Decimal(str(active_notional or "0")), realized, int(time.time() * 1000),
                )
                event_payload = {**exit_payload, "realized_pnl": str(net_pnl), "exit_reason": fill.exit_reason}
                await connection.execute(
                    """
                    insert into signal_lifecycle_events(signal_id, sequence_id, state, event_type, event_time_ms, payload)
                    values($1,3,'EXITING','EXIT_REQUEST',$2,$3::jsonb),
                          ($1,4,'CLOSED','CLOSED',$2,$3::jsonb)
                    on conflict(signal_id, sequence_id) do nothing
                    """,
                    position["decision_id"], candle_time_ms, json.dumps(event_payload),
                )
        return {"status": "CLOSED", "position_id": position_id, "fill_id": exit_fill_id, "realized_pnl": str(net_pnl), "exit_reason": fill.exit_reason}

    async def recover_stale_cycles(self, now_ms: int, stale_after_ms: int) -> list[str]:
        pool = self._require_pool()
        cutoff = now_ms - max(stale_after_ms, 60_000)
        async with pool.acquire() as connection:
            rows = await connection.fetch(
                """
                update cycle_runs
                set status='FAILED', finished_at_ms=$1::bigint, error_count=error_count+1,
                    summary=summary || jsonb_build_object(
                        'recovered_as_stale', true,
                        'recovered_at_ms', $1::bigint,
                        'recovery_reason', 'PROCESS_RESTART_OR_CYCLE_TIMEOUT'
                    )
                where status='RUNNING' and started_at_ms < $2::bigint
                returning cycle_id
                """,
                now_ms,
                cutoff,
            )
        return [str(row["cycle_id"]) for row in rows]

    async def start_cycle(
        self,
        cycle_id: str,
        started_at_ms: int,
        mode: str,
        symbols_requested: int,
        code_version: str,
        config_version: str,
    ) -> None:
        pool = self._require_pool()
        async with pool.acquire() as connection:
            await connection.execute(
                """
                insert into cycle_runs(
                    cycle_id, started_at_ms, status, mode, symbols_requested,
                    code_version, config_version
                ) values($1,$2,'RUNNING',$3,$4,$5,$6)
                on conflict(cycle_id) do nothing
                """,
                cycle_id,
                started_at_ms,
                mode,
                symbols_requested,
                code_version,
                config_version,
            )

    async def record_cycle_event(
        self,
        cycle_id: str,
        sequence_id: int,
        stage: str,
        status: str,
        event_time_ms: int,
        symbol: str | None = None,
        duration_ms: int | None = None,
        reason_codes: list[str] | None = None,
        metrics: dict | None = None,
        error_type: str | None = None,
        error_message: str | None = None,
    ) -> None:
        pool = self._require_pool()
        async with pool.acquire() as connection:
            await connection.execute(
                """
                insert into cycle_events(
                    cycle_id, sequence_id, symbol, stage, status, event_time_ms,
                    duration_ms, reason_codes, metrics, error_type, error_message
                ) values($1,$2,$3,$4,$5,$6,$7,$8::jsonb,$9::jsonb,$10,$11)
                on conflict(cycle_id, sequence_id) do nothing
                """,
                cycle_id,
                sequence_id,
                symbol,
                stage,
                status,
                event_time_ms,
                duration_ms,
                json.dumps(reason_codes or []),
                json.dumps(metrics or {}),
                error_type,
                error_message,
            )

    async def finish_cycle(
        self,
        cycle_id: str,
        finished_at_ms: int,
        status: str,
        symbols_processed: int,
        decisions_count: int,
        orders_created: int,
        error_count: int,
        summary: dict,
    ) -> None:
        pool = self._require_pool()
        async with pool.acquire() as connection:
            await connection.execute(
                """
                update cycle_runs
                set finished_at_ms=$2,
                    status=$3,
                    symbols_processed=$4,
                    decisions_count=$5,
                    orders_created=$6,
                    error_count=$7,
                    summary=$8::jsonb
                where cycle_id=$1
                """,
                cycle_id,
                finished_at_ms,
                status,
                symbols_processed,
                decisions_count,
                orders_created,
                error_count,
                json.dumps(summary),
            )

    async def recent_cycles(self, limit: int = 30, search: str = "") -> list[dict]:
        pool = self._require_pool()
        if not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        search = search.strip().upper()
        async with pool.acquire() as connection:
            rows = await connection.fetch(
                """
                select cycle_id, started_at_ms, finished_at_ms, status, mode,
                       symbols_requested, symbols_processed, decisions_count,
                       orders_created, error_count, code_version, config_version, summary
                from cycle_runs
                where ($2 = '' or upper(cycle_id) like '%' || $2 || '%' or upper(status) like '%' || $2 || '%' or upper(mode) like '%' || $2 || '%')
                order by started_at_ms desc
                limit $1
                """,
                limit,
                search,
            )
        return [_decode_row_json(dict(row), ("summary",)) for row in rows]

    async def cycle_detail(self, cycle_id: str, event_limit: int = 1000) -> dict | None:
        pool = self._require_pool()
        if not cycle_id.strip():
            raise ValueError("cycle_id is required")
        async with pool.acquire() as connection:
            run = await connection.fetchrow(
                """
                select cycle_id, started_at_ms, finished_at_ms, status, mode,
                       symbols_requested, symbols_processed, decisions_count,
                       orders_created, error_count, code_version, config_version, summary
                from cycle_runs
                where cycle_id=$1
                """,
                cycle_id,
            )
            if run is None:
                return None
            events = await connection.fetch(
                """
                select event_id, cycle_id, sequence_id, symbol, stage, status,
                       event_time_ms, duration_ms, reason_codes, metrics,
                       error_type, error_message
                from cycle_events
                where cycle_id=$1
                order by sequence_id asc
                limit $2
                """,
                cycle_id,
                event_limit,
            )
        return {
            "run": _decode_row_json(dict(run), ("summary",)),
            "events": [_decode_row_json(dict(event), ("reason_codes", "metrics")) for event in events],
        }
