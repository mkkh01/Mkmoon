from __future__ import annotations

from decimal import Decimal
from typing import Iterable

from app.core.models import Candle, Decision
from app.core.settings import Settings
from app.engine.decision import evaluate_decision
from app.engine.risk import RiskFilters


def replay_decisions(
    snapshots: Iterable[dict],
    settings: Settings,
    filters_by_symbol: dict[str, RiskFilters],
) -> list[Decision]:
    decisions: list[Decision] = []
    for snapshot in sorted(snapshots, key=lambda item: (item["decision_time_ms"], item["symbol"])):
        candles_by_tf = {
            timeframe: [Candle.model_validate(candle) for candle in candles]
            for timeframe, candles in snapshot["candles_by_timeframe"].items()
        }
        decision = evaluate_decision(
            symbol=snapshot["symbol"],
            candles_by_timeframe=candles_by_tf,
            decision_time_ms=int(snapshot["decision_time_ms"]),
            settings=settings,
            filters=filters_by_symbol[snapshot["symbol"]],
            equity=Decimal(str(snapshot.get("equity", "10000"))),
            remaining_daily_risk=Decimal(str(snapshot.get("remaining_daily_risk", "100"))),
            remaining_portfolio_risk=Decimal(str(snapshot.get("remaining_portfolio_risk", "100"))),
        )
        decisions.append(decision)
    return decisions
