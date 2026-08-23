import pytest

from app.adapters.binance_public import BinancePublicClient
from app.main import DASHBOARD_SYMBOLS


def test_dashboard_has_25_unique_usdt_symbols() -> None:
    assert len(DASHBOARD_SYMBOLS) == 25
    assert len(set(DASHBOARD_SYMBOLS)) == 25
    assert all(symbol.endswith("USDT") for symbol in DASHBOARD_SYMBOLS)


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
