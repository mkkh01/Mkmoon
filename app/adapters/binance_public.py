from __future__ import annotations

import asyncio
import json
import time
from decimal import Decimal
from typing import Any

import httpx

from app.core.models import Candle, ExchangeSymbolFilters


class BinancePublicClient:
    def __init__(self, base_url: str, data_base_url: str, timeout_seconds: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.data_base_url = data_base_url.rstrip("/")
        self.client = httpx.AsyncClient(timeout=timeout_seconds, headers={"User-Agent": "mkmoon/0.1"})

    async def aclose(self) -> None:
        await self.client.aclose()

    async def _get(self, url: str, params: dict[str, Any] | None = None) -> Any:
        last_error: Exception | None = None
        for attempt in range(4):
            try:
                response = await self.client.get(url, params=params)
                if response.status_code == 429:
                    retry_after = min(float(response.headers.get("Retry-After", "1")), 30.0)
                    await asyncio.sleep(retry_after)
                    continue
                if response.status_code >= 500:
                    raise RuntimeError(f"Binance server error {response.status_code}")
                response.raise_for_status()
                return response.json()
            except (httpx.HTTPError, RuntimeError) as exc:
                last_error = exc
                if attempt == 3:
                    break
                await asyncio.sleep(0.5 * (2**attempt))
        raise RuntimeError(f"Binance request failed: {last_error}")

    async def server_time_ms(self) -> int:
        payload = await self._get(f"{self.base_url}/api/v3/time")
        return int(payload["serverTime"])

    async def exchange_info(self, symbols: list[str]) -> dict[str, ExchangeSymbolFilters]:
        symbols_json = json.dumps(symbols, separators=(",", ":"))
        payload = await self._get(f"{self.base_url}/api/v3/exchangeInfo", {"symbols": symbols_json})
        snapshot_time = int(time.time() * 1000)
        result: dict[str, ExchangeSymbolFilters] = {}
        for item in payload.get("symbols", []):
            symbol = item["symbol"]
            if symbol not in symbols:
                continue
            filters = {f["filterType"]: f for f in item.get("filters", [])}
            result[symbol] = ExchangeSymbolFilters(
                symbol=symbol,
                status=item["status"],
                base_asset=item["baseAsset"],
                quote_asset=item["quoteAsset"],
                base_asset_precision=int(item.get("baseAssetPrecision", 8)),
                quote_asset_precision=int(item.get("quoteAssetPrecision", 8)),
                filters=filters,
                snapshot_time_ms=snapshot_time,
                exchange_info_version=f"exchangeInfo:{snapshot_time}",
            )
        return result

    async def closed_klines(self, symbol: str, interval: str, limit: int, decision_time_ms: int | None = None) -> list[Candle]:
        if not 1 <= limit <= 1000:
            raise ValueError("Binance kline limit must be between 1 and 1000")
        decision_time_ms = decision_time_ms or await self.server_time_ms()
        raw = await self._get(
            f"{self.data_base_url}/api/v3/klines",
            {"symbol": symbol, "interval": interval, "limit": limit, "endTime": decision_time_ms},
        )
        candles: list[Candle] = []
        for row in raw:
            candle = Candle(
                symbol=symbol,
                timeframe=interval,
                open_time_ms=int(row[0]),
                close_time_ms=int(row[6]),
                open=Decimal(str(row[1])),
                high=Decimal(str(row[2])),
                low=Decimal(str(row[3])),
                close=Decimal(str(row[4])),
                volume=Decimal(str(row[5])),
                is_closed=int(row[6]) <= decision_time_ms,
            )
            if candle.is_closed:
                candles.append(candle)
        return candles

    async def ping(self) -> bool:
        await self._get(f"{self.base_url}/api/v3/ping")
        return True
