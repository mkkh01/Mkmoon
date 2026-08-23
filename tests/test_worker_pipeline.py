from __future__ import annotations

from contextlib import asynccontextmanager
from decimal import Decimal

import pytest

from app.core.models import Candle, ExchangeSymbolFilters
from app.core.settings import Settings
from app.worker import run_once


class FakeClient:
    public_base_urls = {"https://fake.binance"}
    last_public_base_url = "https://fake.binance"

    async def server_time_ms(self) -> int:
        return 1_800_000_000_000

    async def exchange_info(self, symbols: list[str]) -> dict[str, ExchangeSymbolFilters]:
        return {
            symbol: ExchangeSymbolFilters(
                symbol=symbol,
                status="TRADING",
                base_asset=symbol.removesuffix("USDT"),
                quote_asset="USDT",
                base_asset_precision=8,
                quote_asset_precision=8,
                filters={
                    "PRICE_FILTER": {"tickSize": "0.01"},
                    "LOT_SIZE": {"minQty": "0.001", "maxQty": "100000", "stepSize": "0.001"},
                    "NOTIONAL": {"minNotional": "10"},
                },
                snapshot_time_ms=1_800_000_000_000,
                exchange_info_version="fake-exchange-info-v1",
            )
            for symbol in symbols
        }

    async def closed_klines(self, symbol: str, interval: str, limit: int, decision_time_ms: int | None = None) -> list[Candle]:
        step_ms = {"4h": 14_400_000, "1h": 3_600_000, "15m": 900_000, "5m": 300_000}[interval]
        base = 1_700_000_000_000
        return [
            Candle(
                symbol=symbol,
                timeframe=interval,
                open_time_ms=base + index * step_ms,
                close_time_ms=base + index * step_ms + step_ms - 1,
                open=Decimal("100") + Decimal(index) / Decimal("10"),
                high=Decimal("100.2") + Decimal(index) / Decimal("10"),
                low=Decimal("99.8") + Decimal(index) / Decimal("10"),
                close=Decimal("100.1") + Decimal(index) / Decimal("10"),
                volume=Decimal("100"),
                is_closed=True,
                source_id="https://fake.binance",
            )
            for index in range(limit)
        ]


class FakePostgres:
    pool = object()

    def __init__(self) -> None:
        self.events: list[dict] = []
        self.finished: dict | None = None
        self.saved_candles = 0
        self.saved_decisions = 0

    async def recover_stale_cycles(self, now_ms: int, stale_after_ms: int) -> list[str]:
        return []

    async def start_cycle(self, **kwargs) -> None:
        return None

    async def observed_realized_r(self) -> list[Decimal]:
        return []

    async def record_cycle_event(self, **kwargs) -> None:
        self.events.append(kwargs)

    async def active_paper_position(self, symbol: str) -> None:
        return None

    async def save_candles(self, candles) -> None:
        self.saved_candles += len(list(candles))

    async def save_decision(self, decision) -> None:
        self.saved_decisions += 1

    async def finish_cycle(self, **kwargs) -> None:
        self.finished = kwargs


class FakeRedis:
    client = object()

    def __init__(self) -> None:
        self.published = 0

    @asynccontextmanager
    async def lock(self, name: str, ttl_seconds: int):
        yield

    async def publish_decision(self, decision: dict) -> int:
        self.published += 1
        return 1


@pytest.mark.asyncio
async def test_worker_cycle_processes_symbol_end_to_end_without_orders() -> None:
    postgres = FakePostgres()
    redis = FakeRedis()
    settings = Settings(
        app_env="test",
        trading_mode="paper",
        live_trading_enabled=False,
        symbols="BTCUSDT",
        poll_interval_seconds=5,
        config_path="configs/config.v1.yaml",
    )

    result = await run_once(settings=settings, postgres_store=postgres, redis_store=redis, client=FakeClient())

    assert result.status == "COMPLETED"
    assert result.symbols_requested == 1
    assert result.symbols_processed == 1
    assert result.decisions_count == 1
    assert result.orders_created == 0
    assert result.error_count == 0
    assert postgres.saved_candles == 880
    assert postgres.saved_decisions == 1
    assert redis.published == 1
    assert postgres.finished is not None
    assert postgres.finished["status"] == "COMPLETED"
    assert sum(event["stage"] == "CANDLE_FETCH" and event["status"] == "SUCCESS" for event in postgres.events) == 4
    assert any(event["stage"] == "DECISION_PERSIST" and event["status"] == "SUCCESS" for event in postgres.events)
    assert any(event["stage"] == "PAPER_EXECUTION" and event["status"] == "SKIPPED" for event in postgres.events)


@pytest.mark.asyncio
async def test_worker_requires_redis_lock_in_production() -> None:
    settings = Settings(app_env="production", trading_mode="paper", live_trading_enabled=False, symbols="BTCUSDT")
    result = await run_once(settings=settings, postgres_store=None, redis_store=None, client=FakeClient())
    assert result.status == "FAILED_DEPENDENCY"
    assert result.symbols_processed == 0
    assert result.orders_created == 0
