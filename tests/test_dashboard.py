from pathlib import Path

import pytest

from app.adapters.binance_public import BinancePublicClient
from app.main import DASHBOARD_SYMBOLS


def test_dashboard_has_25_unique_usdt_symbols() -> None:
    assert len(DASHBOARD_SYMBOLS) == 25
    assert len(set(DASHBOARD_SYMBOLS)) == 25
    assert all(symbol.endswith("USDT") for symbol in DASHBOARD_SYMBOLS)


def test_dashboard_uses_websocket_with_safe_fallbacks() -> None:
    html = (Path(__file__).parents[1] / "app" / "static" / "index.html").read_text(encoding="utf-8")
    assert "new WebSocket(MARKET_STREAM_URL)" in html
    assert "@miniTicker" in html
    assert "scheduled rotation" in html
    assert "loadBrowserMarket" in html
    assert "getJson('/api/market/tickers', 4500)" in html


@pytest.mark.asyncio
async def test_ticker_prices_falls_back_to_lightweight_endpoint() -> None:
    client = BinancePublicClient("https://api.binance.com", "https://data-api.binance.vision")
    calls: list[str] = []

    async def fake_get(url: str, params: dict):
        calls.append(url)
        if url.endswith("/ticker/24hr"):
            raise RuntimeError("418")
        return [{"symbol": "BTCUSDT", "price": "70000.12"}]

    client._get = fake_get  # type: ignore[method-assign]
    try:
        result = await client.ticker_prices(["BTCUSDT"])
    finally:
        await client.aclose()

    assert calls == [
        "https://data-api.binance.vision/api/v3/ticker/24hr",
        "https://data-api.binance.vision/api/v3/ticker/price",
    ]
    assert result[0]["symbol"] == "BTCUSDT"
    assert result[0]["price"] == "70000.12"
    assert result[0]["change_percent"] is None


@pytest.mark.asyncio
async def test_ticker_prices_normalizes_public_response() -> None:
    client = BinancePublicClient("https://api.binance.com", "https://data-api.binance.vision")

    async def fake_get(url: str, params: dict):
        assert url.endswith("/api/v3/ticker/24hr")
        assert params["symbols"] == '["BTCUSDT","ETHUSDT"]'
        return [
            {
                "symbol": "BTCUSDT",
                "lastPrice": "70000.12",
                "priceChangePercent": "1.25",
                "highPrice": "71000",
                "lowPrice": "69000",
                "volume": "100",
                "quoteVolume": "7000000",
                "closeTime": 123,
            },
            {"symbol": "IGNORED", "lastPrice": "1"},
        ]

    client._get = fake_get  # type: ignore[method-assign]
    try:
        result = await client.ticker_prices(["btcusdt", "ethusdt"])
    finally:
        await client.aclose()

    assert result == [
        {
            "symbol": "BTCUSDT",
            "price": "70000.12",
            "change_percent": "1.25",
            "high": "71000",
            "low": "69000",
            "volume": "100",
            "quote_volume": "7000000",
            "updated_at_ms": 123,
        }
    ]
