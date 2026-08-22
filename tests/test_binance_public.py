import os

import pytest

from app.adapters.binance_public import BinancePublicClient

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_BINANCE_INTEGRATION") != "1",
    reason="set RUN_BINANCE_INTEGRATION=1 to run live Binance public API checks",
)


@pytest.mark.asyncio
async def test_binance_public_ping_and_klines() -> None:
    client = BinancePublicClient("https://api.binance.com", "https://data-api.binance.vision")
    try:
        assert await client.ping()
        candles = await client.closed_klines("BTCUSDT", "5m", 3)
        assert len(candles) >= 1
        assert all(c.is_closed for c in candles)
        assert all(c.symbol == "BTCUSDT" for c in candles)
    finally:
        await client.aclose()
