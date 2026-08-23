from __future__ import annotations

import asyncio
import json

from app.adapters.binance_public import BinancePublicClient
from app.core.settings import Settings
from app.engine.decision import evaluate_decision
from app.worker import TIMEFRAMES, _risk_filters


async def main() -> None:
    settings = Settings()
    client = BinancePublicClient(settings.binance_base_url, settings.binance_data_base_url)
    try:
        now = await client.server_time_ms()
        exchange = await client.exchange_info(settings.symbol_list())
        results = []
        for symbol in settings.symbol_list():
            info = exchange.get(symbol)
            if info is None or info.status != "TRADING":
                results.append({"symbol": symbol, "status": "UNSAFE", "reason": "SYMBOL_UNAVAILABLE"})
                continue
            candles = {tf: await client.closed_klines(symbol, tf, count, decision_time_ms=now) for tf, count in TIMEFRAMES.items()}
            decision = evaluate_decision(
                symbol=symbol, candles_by_timeframe=candles, decision_time_ms=now,
                settings=settings, filters=_risk_filters(info),
            )
            results.append(decision.model_dump(mode="json"))
        print(json.dumps(results, indent=2, default=str))
    finally:
        await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
