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

    async def ticker_prices(self, symbols: list[str]) -> list[dict[str, Any]]:
        """Return public latest prices, with a lightweight endpoint fallback."""
        normalized = list(dict.fromkeys(symbol.strip().upper() for symbol in symbols if symbol.strip()))
        if not 1 <= len(normalized) <= 100:
            raise ValueError("ticker symbol count must be between 1 and 100")
        params = {"symbols": json.dumps(normalized, separators=(",", ":"))}
        try:
            payload = await self._get(f"{self.data_base_url}/api/v3/ticker/24hr", params)
        except RuntimeError as primary_error:
            # Some hosted egress IPs receive Binance 418 on data-api.vision.
            # Try the lighter public price route, then the configured API host.
            payload = None
            last_error: RuntimeError = primary_error
            fallback_hosts = (
                self.data_base_url,
                self.base_url,
                "https://api1.binance.com",
                "https://api2.binance.com",
                "https://api3.binance.com",
                "https://api4.binance.com",
            )
            for base_url in dict.fromkeys(host.rstrip("/") for host in fallback_hosts):
                try:
                    payload = await self._get(f"{base_url}/api/v3/ticker/price", params)
                    break
                except RuntimeError as exc:
                    last_error = exc
            if payload is None:
                raise last_error
            if isinstance(payload, dict):
                payload = [payload]
            allowed = set(normalized)
            return [
                {
                    "symbol": str(item.get("symbol", "")).upper(),
                    "price": str(item.get("price", "0")),
                    "change_percent": None,
                    "high": None,
                    "low": None,
                    "volume": None,
                    "quote_volume": None,
                    "updated_at_ms": None,
                }
                for item in payload
                if str(item.get("symbol", "")).upper() in allowed
            ]
        if isinstance(payload, dict):
            payload = [payload]
        allowed = set(normalized)
        result: list[dict[str, Any]] = []
        for item in payload:
            symbol = str(item.get("symbol", "")).upper()
            if symbol not in allowed:
                continue
            result.append(
                {
                    "symbol": symbol,
                    "price": str(item.get("lastPrice", "0")),
                    "change_percent": str(item.get("priceChangePercent", "0")),
                    "high": str(item.get("highPrice", "0")),
                    "low": str(item.get("lowPrice", "0")),
                    "volume": str(item.get("volume", "0")),
                    "quote_volume": str(item.get("quoteVolume", "0")),
                    "updated_at_ms": int(item.get("closeTime", 0) or 0),
                }
            )
        return result

    async def ping(self) -> bool:
        await self._get(f"{self.base_url}/api/v3/ping")
        return True
