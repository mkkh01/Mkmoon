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


def test_dashboard_exposes_cycle_summary_timeline() -> None:
    html = (Path(__file__).parents[1] / "app" / "static" / "index.html").read_text(encoding="utf-8")
    assert 'id="cyclesView"' in html
    assert "loadCycles" in html
    assert "/api/cycles/" in html
    assert "cycle_events" not in html  # browser must use API, not query the database directly


def test_dashboard_uses_latest_completed_cycle_and_unambiguous_counts() -> None:
    html = (Path(__file__).parents[1] / "app" / "static" / "index.html").read_text(encoding="utf-8")
    assert "rows.find(row => row.status === 'COMPLETED')" in html
    assert "تمت معالجة" in html
    assert "حلّل ${data.worker_symbols" in html


def test_dashboard_translates_and_disambiguates_diagnostics() -> None:
    html = (Path(__file__).parents[1] / "app" / "static" / "index.html").read_text(encoding="utf-8")
    assert "\\\\n\\\\n" not in html
    assert "تحققت ${totalPass} من ${total}" in html
    assert "Score التغطية" in html
    assert "readableReason" in html
    assert "readableEv" in html
    assert "قيد التحديث" in html
    assert "const reasons=(event.reason_codes||[]).map(readableReason)" in html


def test_cycle_audit_migration_has_run_and_event_contract() -> None:
    sql = (Path(__file__).parents[1] / "migrations" / "0005_cycle_audit.sql").read_text(encoding="utf-8")
    for required in ("create table if not exists cycle_runs", "create table if not exists cycle_events", "reason_codes jsonb", "error_message text"):
        assert required in sql


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
async def test_public_worker_requests_fall_back_to_data_host() -> None:
    client = BinancePublicClient("https://api.binance.com", "https://data-api.binance.vision")
    calls: list[str] = []

    async def fake_get(url: str, params: dict | None = None):
        calls.append(url)
        if url == "https://api.binance.com/api/v3/time":
            raise RuntimeError("418")
        if url == "https://data-api.binance.vision/api/v3/time":
            return {"serverTime": 123456}
        raise AssertionError(url)

    client._get = fake_get  # type: ignore[method-assign]
    try:
        assert await client.server_time_ms() == 123456
    finally:
        await client.aclose()

    assert calls[:2] == [
        "https://api.binance.com/api/v3/time",
        "https://data-api.binance.vision/api/v3/time",
    ]


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
