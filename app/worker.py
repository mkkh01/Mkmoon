from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from app.adapters.binance_public import BinancePublicClient
from app.core.models import DecisionStatus
from app.core.policy import load_policy
from app.core.settings import Settings, get_settings
from app.engine.decision import evaluate_decision
from app.engine.paper import simulate_long_entry, simulate_long_exit_levels
from app.engine.risk import RiskFilters
from app.storage.postgres import PostgresStore
from app.storage.redis import RedisStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("mkmoon.worker")

TIMEFRAMES = {"4h": 220, "1h": 220, "15m": 220, "5m": 220}


@dataclass(frozen=True)
class CycleResult:
    cycle_id: str
    status: str
    started_at_ms: int
    finished_at_ms: int
    duration_ms: int
    symbols_requested: int
    symbols_processed: int
    decisions_count: int
    orders_created: int
    error_count: int
    data_source: str
    errors: tuple[str, ...] = field(default_factory=tuple)


def _decimal(value: object, default: str = "0") -> Decimal:
    return Decimal(str(value if value is not None else default))


def _risk_filters(exchange_symbol) -> RiskFilters:
    filters = exchange_symbol.filters
    price = filters.get("PRICE_FILTER", {})
    lot = filters.get("LOT_SIZE", {})
    notional = filters.get("NOTIONAL") or filters.get("MIN_NOTIONAL") or {}
    return RiskFilters(
        min_qty=_decimal(lot.get("minQty"), "0"),
        max_qty=_decimal(lot.get("maxQty"), "100000000"),
        step_size=_decimal(lot.get("stepSize"), "0.00000001"),
        min_notional=_decimal(notional.get("minNotional"), "0"),
        max_notional=_decimal(notional.get("maxNotional")) if notional.get("maxNotional") else None,
        tick_size=_decimal(price.get("tickSize"), "0"),
    )


def _data_source(client: BinancePublicClient, sources: set[str]) -> str:
    sources.update(getattr(client, "public_base_urls", set()))
    if client.last_public_base_url:
        sources.add(client.last_public_base_url)
    return ",".join(sorted(sources)) or "public_spot_rest_closed_klines"


async def run_once(
    *,
    settings: Settings | None = None,
    postgres_store: PostgresStore | None = None,
    redis_store: RedisStore | None = None,
    client: BinancePublicClient | None = None,
) -> CycleResult:
    """Run one auditable Paper cycle. This function never calls private Binance APIs."""
    settings = settings or get_settings()
    settings.assert_safe_mode()
    symbols = settings.symbol_list()
    own_client = client is None
    own_postgres = postgres_store is None
    own_redis = redis_store is None
    client = client or BinancePublicClient(
        settings.binance_base_url,
        settings.binance_data_base_url,
        fallback_base_url=settings.binance_fallback_base_url,
    )
    postgres = postgres_store or (PostgresStore(settings.database_url) if settings.database_url else None)
    redis = redis_store or (RedisStore(settings.redis_url) if settings.redis_url else None)

    try:
        if postgres and postgres.pool is None:
            await postgres.connect()
        if redis and redis.client is None:
            await redis.connect()
        if settings.app_env == "production" and not (redis and redis.client):
            now = int(time.time() * 1000)
            log.error("paper cycle blocked: Redis is required for the production distributed lock")
            return CycleResult(
                cycle_id=f"cycle-blocked-{now}-{uuid.uuid4().hex[:8]}", status="FAILED_DEPENDENCY",
                started_at_ms=now, finished_at_ms=now, duration_ms=0,
                symbols_requested=len(symbols), symbols_processed=0, decisions_count=0,
                orders_created=0, error_count=1, data_source="not_started",
                errors=("RedisUnavailable",),
            )
        if redis and redis.client:
            try:
                async with redis.lock("paper-cycle", ttl_seconds=settings.worker_lock_ttl_seconds):
                    return await _run_cycle(settings, symbols, client, postgres, redis)
            except TimeoutError:
                now = int(time.time() * 1000)
                log.warning("paper cycle skipped because distributed lock is held")
                return CycleResult(
                    cycle_id=f"cycle-skipped-{now}-{uuid.uuid4().hex[:8]}", status="SKIPPED_LOCKED",
                    started_at_ms=now, finished_at_ms=now, duration_ms=0,
                    symbols_requested=len(symbols), symbols_processed=0, decisions_count=0,
                    orders_created=0, error_count=0, data_source="not_started",
                )
        return await _run_cycle(settings, symbols, client, postgres, redis)
    finally:
        if own_client:
            await client.aclose()
        if own_redis and redis:
            await redis.close()
        if own_postgres and postgres:
            await postgres.close()


async def _run_cycle(
    settings: Settings,
    symbols: list[str],
    client: BinancePublicClient,
    postgres: PostgresStore | None,
    redis: RedisStore | None,
) -> CycleResult:
    cycle_id = f"cycle-{int(time.time() * 1000)}-{uuid.uuid4().hex[:10]}"
    started_at_ms = int(time.time() * 1000)
    policy = load_policy(settings.config_path)
    db_ready = bool(postgres and postgres.pool)
    redis_ready = bool(redis and redis.client)
    audit_disabled = False
    sequence_id = 0
    sources: set[str] = set()
    error_types: list[str] = []
    stats: dict[str, Any] = {
        "symbols_requested": len(symbols), "symbols_processed": 0, "symbols_skipped": 0,
        "symbols_failed": 0, "decisions_count": 0, "orders_created": 0,
        "paper_filled_orders": 0, "execution_skipped": 0, "execution_blocked": 0,
        "error_count": 0, "audit_write_errors": 0, "candle_counts": {},
        "decision_status_counts": {status.value: 0 for status in DecisionStatus},
        "reason_counts": {}, "lock": "redis:paper-cycle" if redis_ready else "unavailable",
        "sources_seen": [],
    }
    emit_lock = asyncio.Lock()

    async def emit(stage: str, status: str, *, symbol: str | None = None,
                   duration_ms: int | None = None, reason_codes: list[str] | None = None,
                   metrics: dict | None = None, error: Exception | None = None) -> None:
        nonlocal sequence_id, audit_disabled
        async with emit_lock:
            sequence_id += 1
            if not db_ready or audit_disabled or postgres is None:
                return
            try:
                await postgres.record_cycle_event(
                    cycle_id=cycle_id, sequence_id=sequence_id, stage=stage, status=status,
                    event_time_ms=int(time.time() * 1000), symbol=symbol, duration_ms=duration_ms,
                    reason_codes=reason_codes or [], metrics=metrics or {},
                    error_type=type(error).__name__ if error else None,
                    error_message=str(error)[:500] if error else None,
                )
            except Exception as audit_error:
                audit_disabled = True
                stats["audit_write_errors"] += 1
                log.exception("cycle audit disabled stage=%s error=%s", stage, type(audit_error).__name__)

    async def persist_error(stage: str, error: Exception, *, symbol: str | None = None) -> None:
        stats["error_count"] += 1
        error_types.append(type(error).__name__)
        await emit(stage, "ERROR", symbol=symbol, error=error)
        log.exception("cycle=%s stage=%s symbol=%s error=%s", cycle_id, stage, symbol or "-", type(error).__name__)

    observed_r: list[Decimal] = []
    fetch_semaphore = asyncio.Semaphore(settings.worker_concurrency)
    fatal = False
    try:
        if postgres and db_ready:
            try:
                recovered = await postgres.recover_stale_cycles(
                    int(time.time() * 1000), settings.cycle_timeout_seconds * 1000 + 60_000
                )
                if recovered:
                    await emit("STALE_CYCLE_RECOVERY", "SUCCESS", metrics={"recovered_cycle_ids": recovered})
                await postgres.start_cycle(
                    cycle_id=cycle_id, started_at_ms=started_at_ms, mode=str(settings.trading_mode),
                    symbols_requested=len(symbols), code_version=str(policy["code_version"]),
                    config_version=str(policy["version"]),
                )
                await emit("CYCLE_START", "SUCCESS", metrics={
                    "symbols_requested": len(symbols), "mode": str(settings.trading_mode),
                    "live_trading_enabled": bool(settings.live_trading_enabled),
                    "code_version": str(policy["code_version"]), "config_version": str(policy["version"]),
                })
                observed_r = await postgres.observed_realized_r()
                await emit("EV_CALIBRATION", "SUCCESS", metrics={"sample_size": len(observed_r)})
            except Exception as error:
                await persist_error("POSTGRES_START_OR_CALIBRATION", error)
        else:
            await emit("CYCLE_START", "UNAVAILABLE", reason_codes=["DATABASE_URL_MISSING_OR_NOT_READY"])

        if redis_ready:
            await emit("REDIS_CONNECT", "SUCCESS")
        else:
            await emit("REDIS_CONNECT", "SKIPPED", reason_codes=["REDIS_URL_MISSING_OR_NOT_READY"])

        try:
            server_time = await asyncio.wait_for(client.server_time_ms(), timeout=settings.request_timeout_seconds)
            _data_source(client, sources)
            await emit("BINANCE_SERVER_TIME", "SUCCESS", metrics={"server_time_ms": server_time})
        except Exception as error:
            await persist_error("BINANCE_SERVER_TIME", error)
            fatal = True
            server_time = int(time.time() * 1000)

        exchange: dict = {}
        if not fatal:
            try:
                exchange = await asyncio.wait_for(client.exchange_info(symbols), timeout=settings.request_timeout_seconds)
                _data_source(client, sources)
                await emit("EXCHANGE_INFO", "SUCCESS", metrics={"symbols_returned": len(exchange), "symbols_requested": len(symbols)})
            except Exception as error:
                await persist_error("EXCHANGE_INFO", error)
                fatal = True

        async def process_symbol(symbol: str) -> None:
            async with asyncio.Semaphore(1):
                info = exchange.get(symbol)
                if info is None or info.status != "TRADING":
                    stats["symbols_skipped"] += 1
                    await emit("SYMBOL_VALIDATE", "SKIPPED", symbol=symbol, reason_codes=["SYMBOL_UNAVAILABLE_OR_NOT_TRADING"])
                    return
                candles_by_tf: dict[str, list] = {}
                fetch_results: list[tuple[str, list, Exception | None, int]] = []

                async def fetch_tf(timeframe: str, limit: int) -> tuple[str, list, Exception | None, int]:
                    fetch_started = time.monotonic()
                    try:
                        candles = await asyncio.wait_for(
                            client.closed_klines(symbol, timeframe, limit, decision_time_ms=server_time),
                            timeout=settings.request_timeout_seconds,
                        )
                        _data_source(client, sources)
                        return timeframe, candles, None, int((time.monotonic() - fetch_started) * 1000)
                    except Exception as error:
                        return timeframe, [], error, int((time.monotonic() - fetch_started) * 1000)

                async def bounded_fetch(tf: str, limit: int):
                    async with fetch_semaphore:
                        return await fetch_tf(tf, limit)

                fetch_results.extend(await asyncio.gather(*(bounded_fetch(tf, limit) for tf, limit in TIMEFRAMES.items())))
                symbol_failed = False
                for timeframe, candles, error, duration_ms in sorted(fetch_results):
                    if error:
                        symbol_failed = True
                        await persist_error("CANDLE_FETCH", error, symbol=symbol)
                        await emit("CANDLE_FETCH", "ERROR", symbol=symbol, duration_ms=duration_ms,
                                   reason_codes=[f"FETCH_FAILED:{timeframe}"], metrics={"timeframe": timeframe}, error=error)
                        continue
                    candles_by_tf[timeframe] = candles
                    close_times = [c.close_time_ms for c in candles]
                    stats["candle_counts"][f"{symbol}:{timeframe}"] = len(candles)
                    await emit("CANDLE_FETCH", "SUCCESS" if candles else "EMPTY", symbol=symbol,
                               duration_ms=duration_ms,
                               reason_codes=[] if candles else [f"NO_CLOSED_CANDLES:{timeframe}"],
                               metrics={"timeframe": timeframe, "requested_limit": TIMEFRAMES[timeframe],
                                        "candle_count": len(candles), "closed_count": sum(c.is_closed for c in candles),
                                        "first_close_time_ms": min(close_times) if close_times else None,
                                        "last_close_time_ms": max(close_times) if close_times else None})
                    if not candles:
                        symbol_failed = True
                if symbol_failed:
                    stats["symbols_failed"] += 1
                    await emit("FEATURE_GATE", "BLOCKED", symbol=symbol, reason_codes=["CANDLE_DATA_INCOMPLETE"],
                               metrics={"timeframes_received": sorted(candles_by_tf)})
                    return

                stats["symbols_processed"] += 1
                if postgres and db_ready and "5m" in candles_by_tf:
                    try:
                        position = await postgres.active_paper_position(symbol)
                        if position:
                            opened_at_ms = int(position["opened_at_ms"])
                            eligible_candles = [
                                candle for candle in candles_by_tf["5m"]
                                if candle.close_time_ms > opened_at_ms
                            ]
                            if eligible_candles:
                                last_candle = max(eligible_candles, key=lambda candle: candle.close_time_ms)
                                exit_fill = simulate_long_exit_levels(
                                    entry_price=Decimal(str(position["entry_price"])),
                                    stop_price=Decimal(str(position["stop_price"])),
                                    target_price=Decimal(str(position["target_price"])),
                                    quantity=Decimal(str(position["quantity"])), candle=last_candle,
                                    fee_rate=Decimal(str(policy["risk"]["fee_rate"])),
                                )
                                if exit_fill.status == "CLOSED":
                                    closed = await postgres.close_paper_position(
                                        position, exit_fill, last_candle.close_time_ms,
                                        {"data_source": _data_source(client, sources), "candle_time_ms": last_candle.close_time_ms},
                                    )
                                    await emit("PAPER_EXIT", closed["status"], symbol=symbol,
                                               reason_codes=[exit_fill.exit_reason or "EXIT"], metrics=closed)
                    except Exception as error:
                        await persist_error("PAPER_EXIT", error, symbol=symbol)

                decision_started = time.monotonic()
                try:
                    decision = evaluate_decision(
                        symbol=symbol, candles_by_timeframe=candles_by_tf, decision_time_ms=server_time,
                        settings=settings, filters=_risk_filters(info), observed_r=observed_r,
                        data_source=_data_source(client, sources),
                    )
                except Exception as error:
                    await persist_error("DECISION_EVALUATE", error, symbol=symbol)
                    return
                stats["decisions_count"] += 1
                status_value = decision.status.value
                stats["decision_status_counts"][status_value] += 1
                for reason in decision.reason_codes:
                    stats["reason_counts"][reason] = stats["reason_counts"].get(reason, 0) + 1
                await emit("DECISION_EVALUATE", status_value, symbol=symbol,
                           duration_ms=int((time.monotonic() - decision_started) * 1000),
                           reason_codes=decision.reason_codes,
                           metrics={"data_cutoff_ms": decision.data_cutoff_ms,
                                    "quality_score": str(decision.quality_score) if decision.quality_score is not None else None,
                                    "setup": decision.setup.value if decision.setup else None,
                                    "ev_status": decision.ev_status, "ev_sample_size": decision.ev_sample_size,
                                    "data_source": decision.lineage.get("data_source")})

                persisted = False
                if postgres and db_ready and settings.candle_persistence_enabled:
                    try:
                        all_candles = [candle for candles in candles_by_tf.values() for candle in candles]
                        await postgres.save_candles(all_candles)
                        persisted = True
                        await emit("CANDLE_PERSIST", "SUCCESS", symbol=symbol, metrics={"candle_count": len(all_candles)})
                    except Exception as error:
                        await persist_error("CANDLE_PERSIST", error, symbol=symbol)
                    try:
                        await postgres.save_decision(decision)
                        persisted = True
                        await emit("DECISION_PERSIST", "SUCCESS", symbol=symbol, metrics={"decision_id": decision.decision_id})
                    except Exception as error:
                        persisted = False
                        await persist_error("DECISION_PERSIST", error, symbol=symbol)
                else:
                    await emit("PERSISTENCE", "SKIPPED", symbol=symbol, reason_codes=["DATABASE_NOT_READY_OR_CANDLE_PERSISTENCE_DISABLED"])

                if redis and redis_ready:
                    try:
                        await redis.publish_decision(decision.model_dump(mode="json"))
                        await emit("REDIS_PUBLISH", "SUCCESS", symbol=symbol, metrics={"decision_id": decision.decision_id})
                    except Exception as error:
                        await persist_error("REDIS_PUBLISH", error, symbol=symbol)
                else:
                    await emit("REDIS_PUBLISH", "SKIPPED", symbol=symbol, reason_codes=["REDIS_NOT_READY"])

                if decision.status == DecisionStatus.ENTER and persisted and postgres and db_ready:
                    try:
                        entry = simulate_long_entry(
                            decision,
                            fee_rate=Decimal(str(policy["risk"]["fee_rate"])),
                            slippage_rate=Decimal(str(policy["risk"]["slippage_rate"])),
                        )
                        if entry is None:
                            stats["execution_blocked"] += 1
                            await emit("PAPER_EXECUTION", "BLOCKED", symbol=symbol, reason_codes=["INVALID_ENTRY_PLAN"])
                        else:
                            result = await postgres.create_paper_trade(decision, entry, _data_source(client, sources))
                            if result.get("status") in {"FILLED", "REJECTED"}:
                                stats["orders_created"] += 1
                            if result.get("status") == "FILLED":
                                stats["paper_filled_orders"] += 1
                            await emit("PAPER_EXECUTION", result.get("status", "UNKNOWN"), symbol=symbol,
                                       reason_codes=[] if result.get("status") == "FILLED" else [result.get("reason", "PAPER_NOT_FILLED")],
                                       metrics=result)
                    except Exception as error:
                        await persist_error("PAPER_EXECUTION", error, symbol=symbol)
                else:
                    stats["execution_skipped"] += 1
                    reason = f"DECISION_STATUS:{status_value}" if decision.status != DecisionStatus.ENTER else "PERSISTENCE_NOT_READY"
                    await emit("PAPER_EXECUTION", "SKIPPED", symbol=symbol, reason_codes=[reason], metrics={"decision_id": decision.decision_id})

        if not fatal:
            symbol_results = await asyncio.gather(
                *(process_symbol(symbol) for symbol in symbols), return_exceptions=True
            )
            for symbol, result in zip(symbols, symbol_results):
                if isinstance(result, Exception):
                    await persist_error("SYMBOL_UNHANDLED", result, symbol=symbol)
    except asyncio.CancelledError:
        raise
    except Exception as error:
        await persist_error("CYCLE_UNHANDLED", error)
    finally:
        finished_at_ms = int(time.time() * 1000)
        stats["sources_seen"] = sorted(sources)
        if fatal or (stats["error_count"] > 0 and stats["decisions_count"] == 0):
            cycle_status = "FAILED"
        elif stats["error_count"] == 0 and stats["symbols_failed"] == 0 and stats["symbols_skipped"] == 0:
            cycle_status = "COMPLETED"
        else:
            cycle_status = "PARTIAL"
        summary = {
            "cycle_id": cycle_id, "started_at_ms": started_at_ms, "finished_at_ms": finished_at_ms,
            "duration_ms": finished_at_ms - started_at_ms, "mode": str(settings.trading_mode),
            "live_trading_enabled": bool(settings.live_trading_enabled),
            "binance_data_source": _data_source(client, sources), "redis_connected": redis_ready,
            "postgres_connected": db_ready, "audit_disabled": audit_disabled, **stats,
        }
        if postgres and db_ready:
            try:
                await postgres.finish_cycle(
                    cycle_id=cycle_id, finished_at_ms=finished_at_ms, status=cycle_status,
                    symbols_processed=stats["symbols_processed"], decisions_count=stats["decisions_count"],
                    orders_created=stats["orders_created"], error_count=stats["error_count"] + stats["audit_write_errors"],
                    summary=summary,
                )
            except Exception as error:
                log.exception("cycle=%s finish audit failed error=%s", cycle_id, type(error).__name__)
                error_types.append(type(error).__name__)
        log.info("cycle=%s finished status=%s duration_ms=%s decisions=%s errors=%s", cycle_id, cycle_status,
                 summary["duration_ms"], stats["decisions_count"], stats["error_count"])
    return CycleResult(
        cycle_id=cycle_id, status=cycle_status, started_at_ms=started_at_ms, finished_at_ms=finished_at_ms,
        duration_ms=finished_at_ms - started_at_ms, symbols_requested=len(symbols),
        symbols_processed=stats["symbols_processed"], decisions_count=stats["decisions_count"],
        orders_created=stats["orders_created"], error_count=stats["error_count"] + stats["audit_write_errors"],
        data_source=_data_source(client, sources), errors=tuple(error_types[-20:]),
    )


async def main() -> None:
    settings = get_settings()
    log.info("starting Mkmoon worker mode=%s symbols=%s", settings.trading_mode, settings.symbol_list())
    while True:
        started = time.monotonic()
        try:
            await asyncio.wait_for(run_once(settings=settings), timeout=settings.cycle_timeout_seconds)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("worker cycle failed; no orders are sent")
        elapsed = time.monotonic() - started
        await asyncio.sleep(max(5, settings.poll_interval_seconds - int(elapsed)))


if __name__ == "__main__":
    asyncio.run(main())
