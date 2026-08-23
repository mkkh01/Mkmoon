from __future__ import annotations

import pytest

from app.integrations.telegram_bot import TelegramBot


async def empty_summary() -> dict:
    return {
        "service": "ok",
        "database": "connected",
        "redis": "connected",
        "trading_mode": "paper",
        "live_trading_enabled": False,
        "paper_worker": {"last_cycle_status": "COMPLETED"},
        "worker_symbols_processed": 25,
        "worker_symbols": 25,
    }


async def empty_cycle() -> dict:
    return {
        "run": {
            "status": "COMPLETED",
            "symbols_processed": 25,
            "symbols_requested": 25,
            "decisions_count": 25,
            "orders_created": 0,
            "error_count": 0,
            "summary": {
                "duration_ms": 20000,
                "binance_data_source": "https://api.binance.com",
                "reason_counts": {"SETUP_INCOMPLETE": 25},
                "decision_status_counts": {"WATCH": 25},
                "symbol_diagnostics": {
                    "BTCUSDT": {
                        "decision_status": "WATCH",
                        "reason_codes": ["SETUP_INCOMPLETE"],
                        "strategy_diagnostics": [
                            {
                                "strategy": "TREND_PULLBACK",
                                "passed_conditions": 7,
                                "total_conditions": 10,
                                "score": "70",
                                "first_failed_condition_ar": "قوة الاتجاه",
                            }
                        ],
                    }
                },
            },
        },
        "events": [],
    }


async def empty_rows() -> list[dict]:
    return []


def make_bot(**kwargs) -> TelegramBot:
    return TelegramBot(
        "test-token",
        allowed_chat_ids={12345},
        get_summary=empty_summary,
        get_cycle=empty_cycle,
        get_decisions=empty_rows,
        get_orders=empty_rows,
        get_cycles=empty_rows,
        get_logs=empty_rows,
        **kwargs,
    )


@pytest.mark.asyncio
async def test_webhook_secret_and_private_allowlist_fail_closed() -> None:
    bot = make_bot(webhook_secret="secret-value")
    try:
        assert bot.verify_webhook_secret("secret-value")
        assert not bot.verify_webhook_secret("wrong")
        assert bot.is_allowed_chat(12345, "private")
        assert not bot.is_allowed_chat(12345, "group")
        assert not bot.is_allowed_chat(99999, "private")
    finally:
        await bot.close()


def test_keyboard_contains_read_only_actions_only() -> None:
    keyboard = TelegramBot.main_keyboard()
    labels = [button["text"] for row in keyboard["keyboard"] for button in row]
    assert "حالة النظام" in labels
    assert "آخر دورة" in labels
    assert "الأسعار الحية" in labels
    assert "الصفقات الورقية" in labels
    assert not any(label in labels for label in ("شراء", "بيع", "تفعيل Live", "Force ENTER"))


@pytest.mark.asyncio
async def test_start_sends_menu_without_raw_token() -> None:
    bot = make_bot()
    sent: list[tuple[str, dict | None]] = []

    async def fake_api(method: str, payload: dict) -> dict:
        sent.append((method, payload))
        return {"ok": True, "result": {}}

    bot._api = fake_api  # type: ignore[method-assign]
    try:
        await bot.handle_update({"message": {"chat": {"id": 12345, "type": "private"}, "text": "/start"}})
        assert sent and sent[0][0] == "sendMessage"
        assert "test-token" not in str(sent)
        assert "مرحبًا بك في Mkmoon" in sent[0][1]["text"]
    finally:
        await bot.close()


@pytest.mark.asyncio
async def test_live_prices_view_is_read_only_and_formats_public_tickers() -> None:
    bot = make_bot(get_tickers=lambda: _sample_tickers())
    sent: list[tuple[str, dict]] = []

    async def fake_api(method: str, payload: dict) -> dict:
        sent.append((method, payload))
        return {"ok": True, "result": {}}

    bot._api = fake_api  # type: ignore[method-assign]
    try:
        await bot.handle_update({"message": {"chat": {"id": 12345, "type": "private"}, "text": "الأسعار الحية"}})
        text = "\n".join(payload.get("text", "") for _, payload in sent)
        assert "الأسعار الحية" in text
        assert "BTCUSDT" in text
        assert "70000.12" in text
        assert "1.25%" in text
        assert "لا تُشغّل دورة Worker" in text
        assert all(method == "sendMessage" for method, _ in sent)
    finally:
        await bot.close()


async def _sample_tickers() -> list[dict]:
    return [
        {"symbol": "BTCUSDT", "price": "70000.12", "change_percent": "1.25"},
        {"symbol": "ETHUSDT", "price": "3500", "change_percent": None},
    ]


@pytest.mark.asyncio
async def test_read_only_views_translate_reasons_and_strategy_scores() -> None:
    bot = make_bot()
    sent: list[tuple[str, dict]] = []

    async def fake_api(method: str, payload: dict) -> dict:
        sent.append((method, payload))
        return {"ok": True, "result": {}}

    bot._api = fake_api  # type: ignore[method-assign]
    try:
        await bot.handle_update({"message": {"chat": {"id": 12345, "type": "private"}, "text": "لماذا لا توجد صفقة؟"}})
        await bot.handle_update({"message": {"chat": {"id": 12345, "type": "private"}, "text": "تشخيص 25 رمزًا"}})
        all_text = "\n".join(payload.get("text", "") for _, payload in sent)
        assert "شروط الاستراتيجية غير مكتملة" in all_text
        assert "TREND_PU" not in all_text
        assert "7/10" in all_text
    finally:
        await bot.close()


@pytest.mark.asyncio
async def test_startup_sets_https_webhook_with_restricted_updates() -> None:
    bot = make_bot()
    calls: list[tuple[str, dict]] = []

    async def fake_api(method: str, payload: dict) -> dict:
        calls.append((method, payload))
        return {"ok": True, "result": {"username": "mkmoon_test"}}

    bot._api = fake_api  # type: ignore[method-assign]
    try:
        await bot.startup("https://mkmoon.onrender.com")
        assert calls[0][0] == "getMe"
        assert calls[1][0] == "setWebhook"
        assert calls[1][1]["url"] == "https://mkmoon.onrender.com/telegram/webhook"
        assert calls[1][1]["allowed_updates"] == ["message", "callback_query"]
        assert calls[1][1]["secret_token"] == "secret-value" or len(calls[1][1]["secret_token"]) == 64
    finally:
        await bot.close()
