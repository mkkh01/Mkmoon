import asyncio
import logging
import time
import uuid
from decimal import Decimal

from app.adapters.binance_public import BinancePublicClient
from app.core.policy import load_policy
from app.core.settings import get_settings
from app.core.models import DecisionStatus
from app.engine.decision import evaluate_decision
from app.engine.risk import RiskFilters
from app.storage.postgres import PostgresStore
from app.storage.redis import RedisStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("mkmoon.worker")

TIMEFRAMES = {"4h": 220, "1h": 220, "15m": 220, "5m": 220}


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


async def run_once() -> None:
    settings = get_settings()
    symbols = settings.symbol_list()
    cycle_id = f"cycle-{int(time.time() * 1000)}-{uuid.uuid4().hex[:10]}"
    started_at_ms = int(time.time() * 1000)
    client = BinancePublicClient(settings.binance_base_url, settings.binance_data_base_url)
    postgres = PostgresStore(settings.database_url) if settings.database_url else None
    redis_store = RedisStore(settings.redis_url) if settings.redis_url else None
    db_ready = False
    redis_ready = False
    audit_disabled = False
    sequence_id = 0
    stats = {
        "symbols_requested": len(symbols),
        "symbols_processed": 0,
        "symbols_skipped": 0,
        "symbols_failed": 0,
        "decisions_count": 0,
        "orders_created": 0,
        "execution_skipped": 0,
        "execution_blocked": 0,
        "error_count": 0,
        "audit_write_errors": 0,
        "candle_counts": {},
        "decision_status_counts": {status.value: 0 for status in DecisionStatus},
        "reason_counts": {},
    }

    async def emit(
        stage: str,
        status: str,
        *,
        symbol: str | None = None,
        duration_ms: int | None = None,
        reason_codes: list[str] | None = None,
        metrics: dict | None = None,
        error: Exception | None = None,
    ) -> None:
        nonlocal sequence_id, audit_disabled
        sequence_id += 1
        safe_error_message = None
        error_type = None
        if error is not None:
            error_type = type(error).__name__
            safe_error_message = str(error)[:500]
        if not db_ready or audit_disabled:
            return
        try:
            await postgres.record_cycle_event(
                cycle_id=cycle_id,
                sequence_id=sequence_id,
                stage=stage,
                status=status,
                event_time_ms=int(time.time() * 1000),
                symbol=symbol,
                duration_ms=duration_ms,
                reason_codes=reason_codes or [],
                metrics=metrics or {},
                error_type=error_type,
                error_message=safe_error_message,
            )
        except Exception as audit_error:
            audit_disabled = True
            stats["audit_write_errors"] += 1
            log.exception("cycle audit write disabled stage=%s error=%s", stage, type(audit_error).__name__)

    async def persist_error(stage: str, error: Exception, *, symbol: str | None = None) -> None:
        stats["error_count"] += 1
        await emit(stage, "ERROR", symbol=symbol, error=error)
        log.exception("cycle=%s stage=%s symbol=%s error=%s", cycle_id, stage, symbol or "-", type(error).__name__)

    try:
        policy = load_policy(settings.config_path)
        if postgres:
            try:
                await postgres.connect()
                db_ready = True
                await postgres.start_cycle(
                    cycle_id=cycle_id,
                    started_at_ms=started_at_ms,
                    mode=str(settings.trading_mode),
                    symbols_requested=len(symbols),
                    code_version=str(policy["code_version"]),
                    config_version=str(policy["version"]),
                )
                await emit(
                    "CYCLE_START",
                    "SUCCESS",
                    metrics={
                        "symbols_requested": len(symbols),
                        "mode": str(settings.trading_mode),
                        "live_trading_enabled": bool(settings.live_trading_enabled),
                        "code_version": str(policy["code_version"]),
                        "config_version": str(policy["version"]),
                    },
                )
            except Exception as error:
                await persist_error("POSTGRES_CONNECT", error)
        else:
            await emit("CYCLE_START", "UNAVAILABLE", reason_codes=["DATABASE_URL_MISSING"])

        if redis_store:
            try:
                await redis_store.connect()
                redis_ready = True
                await emit("REDIS_CONNECT", "SUCCESS")
            except Exception as error:
                await persist_error("REDIS_CONNECT", error)
        else:
            await emit("REDIS_CONNECT", "SKIPPED", reason_codes=["REDIS_URL_MISSING"])

        try:
            server_time = await client.server_time_ms()
            await emit("BINANCE_SERVER_TIME", "SUCCESS", metrics={"server_time_ms": server_time})
        except Exception as error:
            await persist_error("BINANCE_SERVER_TIME", error)
            return

        try:
            exchange = await client.exchange_info(symbols)
            await emit(
                "EXCHANGE_INFO",
                "SUCCESS",
                metrics={"symbols_returned": len(exchange), "symbols_requested": len(symbols)},
            )
        except Exception as error:
            await persist_error("EXCHANGE_INFO", error)
            return

        for symbol in symbols:
            info = exchange.get(symbol)
            if info is None or info.status != "TRADING":
                stats["symbols_skipped"] += 1
                await emit(
                    "SYMBOL_VALIDATE",
                    "SKIPPED",
                    symbol=symbol,
                    reason_codes=["SYMBOL_UNAVAILABLE_OR_NOT_TRADING"],
                )
                log.warning("cycle=%s skip %s: symbol unavailable or not trading", cycle_id, symbol)
                continue

            candles_by_tf = {}
            symbol_failed = False
            for timeframe, limit in TIMEFRAMES.items():
                fetch_started = time.monotonic()
                try:
                    candles = await client.closed_klines(
                        symbol,
                        timeframe,
                        limit,
                        decision_time_ms=server_time,
                    )
                    candles_by_tf[timeframe] = candles
                    close_times = [c.close_time_ms for c in candles]
                    tf_metrics = {
                        "timeframe": timeframe,
                        "requested_limit": limit,
                        "candle_count": len(candles),
                        "closed_count": sum(1 for candle in candles if candle.is_closed),
                        "first_close_time_ms": min(close_times) if close_times else None,
                        "last_close_time_ms": max(close_times) if close_times else None,
                    }
                    stats["candle_counts"][f"{symbol}:{timeframe}"] = len(candles)
                    await emit(
                        "CANDLE_FETCH",
                        "SUCCESS" if candles else "EMPTY",
                        symbol=symbol,
                        duration_ms=int((time.monotonic() - fetch_started) * 1000),
                        reason_codes=[] if candles else [f"NO_CLOSED_CANDLES:{timeframe}"],
                        metrics=tf_metrics,
                    )
                    if not candles:
                        symbol_failed = True
                except Exception as error:
                    symbol_failed = True
                    await persist_error("CANDLE_FETCH", error, symbol=symbol)
                    await emit(
                        "CANDLE_FETCH",
                        "ERROR",
                        symbol=symbol,
                        duration_ms=int((time.monotonic() - fetch_started) * 1000),
                        reason_codes=[f"FETCH_FAILED:{timeframe}"],
                        metrics={"timeframe": timeframe, "requested_limit": limit},
                        error=error,
                    )

            if symbol_failed:
                stats["symbols_failed"] += 1
                await emit(
                    "FEATURE_GATE",
                    "BLOCKED",
                    symbol=symbol,
                    reason_codes=["CANDLE_DATA_INCOMPLETE"],
                    metrics={"timeframes_received": sorted(candles_by_tf)},
                )
                continue

            stats["symbols_processed"] += 1
            decision_started = time.monotonic()
            try:
                decision = evaluate_decision(
                    symbol=symbol,
                    candles_by_timeframe=candles_by_tf,
                    decision_time_ms=server_time,
                    settings=settings,
                    filters=_risk_filters(info),
                )
            except Exception as error:
                await persist_error("DECISION_EVALUATE", error, symbol=symbol)
                continue

            stats["decisions_count"] += 1
            status_value = decision.status.value
            stats["decision_status_counts"][status_value] += 1
            for reason in decision.reason_codes:
                stats["reason_counts"][reason] = stats["reason_counts"].get(reason, 0) + 1
            await emit(
                "DECISION_EVALUATE",
                status_value,
                symbol=symbol,
                duration_ms=int((time.monotonic() - decision_started) * 1000),
                reason_codes=decision.reason_codes,
                metrics={
                    "data_cutoff_ms": decision.data_cutoff_ms,
                    "quality_score": str(decision.quality_score) if decision.quality_score is not None else None,
                    "setup": decision.setup.value if decision.setup else None,
                    "ev_status": decision.ev_status,
                    "ev_sample_size": decision.ev_sample_size,
                },
            )

            if postgres and db_ready and settings.candle_persistence_enabled:
                try:
                    all_candles = [candle for candles in candles_by_tf.values() for candle in candles]
                    await postgres.save_candles(all_candles)
                    await emit(
                        "CANDLE_PERSIST",
                        "SUCCESS",
                        symbol=symbol,
                        metrics={"candle_count": len(all_candles)},
                    )
                except Exception as error:
                    await persist_error("CANDLE_PERSIST", error, symbol=symbol)
                try:
                    await postgres.save_decision(decision)
                    await emit("DECISION_PERSIST", "SUCCESS", symbol=symbol, metrics={"decision_id": decision.decision_id})
                except Exception as error:
                    await persist_error("DECISION_PERSIST", error, symbol=symbol)
            else:
                await emit(
                    "PERSISTENCE",
                    "SKIPPED",
                    symbol=symbol,
                    reason_codes=["DATABASE_NOT_READY_OR_CANDLE_PERSISTENCE_DISABLED"],
                )

            if redis_store and redis_ready:
                try:
                    await redis_store.publish_decision(decision.model_dump(mode="json"))
                    await emit("REDIS_PUBLISH", "SUCCESS", symbol=symbol, metrics={"decision_id": decision.decision_id})
                except Exception as error:
                    await persist_error("REDIS_PUBLISH", error, symbol=symbol)
            else:
                await emit("REDIS_PUBLISH", "SKIPPED", symbol=symbol, reason_codes=["REDIS_NOT_READY"])

            if decision.status == DecisionStatus.ENTER:
                stats["execution_blocked"] += 1
                await emit(
                    "PAPER_EXECUTION",
                    "BLOCKED",
                    symbol=symbol,
                    reason_codes=["PAPER_EXECUTOR_NOT_WIRED_IN_WORKER"],
                    metrics={"decision_id": decision.decision_id},
                )
            else:
                stats["execution_skipped"] += 1
                await emit(
                    "PAPER_EXECUTION",
                    "SKIPPED",
                    symbol=symbol,
                    reason_codes=[f"DECISION_STATUS:{status_value}"],
                    metrics={"decision_id": decision.decision_id},
                )

            log.info(
                "cycle=%s symbol=%s status=%s score=%s reasons=%s",
                cycle_id,
                symbol,
                decision.status,
                decision.quality_score,
                decision.reason_codes,
            )
    except Exception as error:
        await persist_error("CYCLE_UNHANDLED", error)
    finally:
        finished_at_ms = int(time.time() * 1000)
        if stats["error_count"] == 0 and stats["symbols_failed"] == 0 and stats["symbols_skipped"] == 0:
            cycle_status = "COMPLETED"
        elif stats["decisions_count"] > 0 or stats["symbols_processed"] > 0:
            cycle_status = "PARTIAL"
        else:
            cycle_status = "FAILED"
        summary = {
            "cycle_id": cycle_id,
            "started_at_ms": started_at_ms,
            "finished_at_ms": finished_at_ms,
            "duration_ms": finished_at_ms - started_at_ms,
            "mode": str(settings.trading_mode),
            "live_trading_enabled": bool(settings.live_trading_enabled),
            "binance_data_source": "public_spot_rest_closed_klines",
            "redis_connected": redis_ready,
            "postgres_connected": db_ready,
            "audit_disabled": audit_disabled,
            **stats,
        }
        if postgres and db_ready:
            try:
                await postgres.finish_cycle(
                    cycle_id=cycle_id,
                    finished_at_ms=finished_at_ms,
                    status=cycle_status,
                    symbols_processed=stats["symbols_processed"],
                    decisions_count=stats["decisions_count"],
                    orders_created=stats["orders_created"],
                    error_count=stats["error_count"] + stats["audit_write_errors"],
                    summary=summary,
                )
            except Exception as error:
                log.exception("cycle=%s finish audit failed error=%s", cycle_id, type(error).__name__)
        log.info(
            "cycle=%s finished status=%s duration_ms=%s decisions=%s errors=%s",
            cycle_id,
            cycle_status,
            summary["duration_ms"],
            stats["decisions_count"],
            stats["error_count"],
        )
        await client.aclose()
        if redis_store:
            await redis_store.close()
        if postgres:
            await postgres.close()


async def main() -> None:
    settings = get_settings()
    log.info("starting Mkmoon worker mode=%s symbols=%s", settings.trading_mode, settings.symbol_list())
    while True:
        started = time.monotonic()
        try:
            await run_once()
        except Exception:
            log.exception("worker cycle failed; no orders are sent")
        elapsed = time.monotonic() - started
        await asyncio.sleep(max(5, settings.poll_interval_seconds - int(elapsed)))


if __name__ == "__main__":
    asyncio.run(main())
