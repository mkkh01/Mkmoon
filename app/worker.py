from __future__ import annotations

import asyncio
import logging
import time
from decimal import Decimal

from app.adapters.binance_public import BinancePublicClient
from app.engine.decision import evaluate_decision
from app.core.settings import get_settings
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
    client = BinancePublicClient(settings.binance_base_url, settings.binance_data_base_url)
    postgres = PostgresStore(settings.database_url) if settings.database_url else None
    redis_store = RedisStore(settings.redis_url) if settings.redis_url else None
    try:
        if postgres:
            await postgres.connect()
        if redis_store:
            await redis_store.connect()
        server_time = await client.server_time_ms()
        exchange = await client.exchange_info(settings.symbol_list())
        for symbol in settings.symbol_list():
            info = exchange.get(symbol)
            if info is None or info.status != "TRADING":
                log.warning("skip %s: symbol unavailable or not trading", symbol)
                continue
            candles_by_tf = {
                tf: await client.closed_klines(symbol, tf, limit)
                for tf, limit in TIMEFRAMES.items()
            }
            decision = evaluate_decision(
                symbol=symbol,
                candles_by_timeframe=candles_by_tf,
                decision_time_ms=server_time,
                settings=settings,
                filters=_risk_filters(info),
            )
            if postgres and settings.candle_persistence_enabled:
                all_candles = [c for candles in candles_by_tf.values() for c in candles]
                await postgres.save_candles(all_candles)
                await postgres.save_decision(decision)
            if redis_store:
                await redis_store.publish_decision(decision.model_dump(mode="json"))
            log.info("%s status=%s score=%s reasons=%s", symbol, decision.status, decision.quality_score, decision.reason_codes)
    finally:
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
