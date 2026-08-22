import pytest
from decimal import Decimal

from app.adapters.binance_private import BinancePrivateClient


@pytest.mark.asyncio
async def test_live_order_is_blocked_by_default() -> None:
    client = BinancePrivateClient("https://api.binance.com", "key", "secret")
    try:
        with pytest.raises(PermissionError):
            await client.place_limit_buy(
                symbol="BTCUSDT",
                quantity=Decimal("0.001"),
                price=Decimal("100"),
                client_order_id="test-order",
                live_trading_enabled=False,
                trading_mode="paper",
            )
    finally:
        await client.aclose()
