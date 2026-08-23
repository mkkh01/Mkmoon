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


@pytest.mark.asyncio
async def test_live_cancel_is_blocked_by_default() -> None:
    client = BinancePrivateClient("https://api.binance.com", "key", "secret")
    try:
        with pytest.raises(PermissionError):
            await client.cancel_order(
                symbol="BTCUSDT",
                orig_client_order_id="test-order",
                live_trading_enabled=False,
                trading_mode="paper",
            )
    finally:
        await client.aclose()


def test_dashboard_log_redaction_removes_full_urls_and_secret_values() -> None:
    from app.main import DashboardLogHandler

    message = "postgresql://user:password@host:5432/db redis://default:secret@host:6379 token=abc123 password=xyz"
    redacted = DashboardLogHandler._redact(message)
    assert "postgresql://" not in redacted
    assert "redis://" not in redacted
    assert "password@host" not in redacted
    assert "secret@host" not in redacted
    assert "abc123" not in redacted
    assert "xyz" not in redacted
    assert "token=[redacted]" in redacted
    assert "password=[redacted]" in redacted
