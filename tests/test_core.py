from datetime import datetime, timezone
from decimal import Decimal

from app.core.hashing import canonical_json, decision_hash
from app.core.models import Candle
from app.core.settings import Settings
from app.engine.decision import evaluate_decision
from app.engine.risk import RiskFilters, calculate_risk_plan, floor_to_step


def candle(symbol: str, timeframe: str, index: int, close: str, closed: bool = True) -> Candle:
    price = Decimal(close)
    return Candle(
        symbol=symbol,
        timeframe=timeframe,
        open_time_ms=index * 60_000,
        close_time_ms=index * 60_000 + 59_000,
        open=price - Decimal("0.1"),
        high=price + Decimal("0.2"),
        low=price - Decimal("0.2"),
        close=price,
        volume=Decimal("100"),
        is_closed=closed,
    )


def test_floor_to_step_never_rounds_up() -> None:
    assert floor_to_step(Decimal("1.239"), Decimal("0.01")) == Decimal("1.23")


def test_hash_is_canonical_and_order_independent() -> None:
    first = {"b": Decimal("1.20"), "a": 1}
    second = {"a": 1, "b": Decimal("1.20")}
    assert canonical_json(first) == canonical_json(second)
    assert decision_hash(first) == decision_hash(second)


def test_risk_plan_cannot_exceed_cash_cap_after_rounding() -> None:
    plan = calculate_risk_plan(
        equity=Decimal("10000"), risk_pct=Decimal("0.01"), remaining_daily_risk=Decimal("100"),
        remaining_portfolio_risk=Decimal("100"), symbol_risk_cap=Decimal("100"),
        cluster_risk_cap=Decimal("100"), entry_price=Decimal("100"), stop_price=Decimal("99"),
        target_price=Decimal("102"), fee_rate=Decimal("0.001"), slippage_rate=Decimal("0.0005"),
        filters=RiskFilters(Decimal("0.001"), Decimal("1000"), Decimal("0.001"), Decimal("10")),
    )
    assert plan.effective_risk_cash <= plan.risk_cash
    assert plan.quantity > 0


def test_decision_with_missing_critical_data_is_unsafe() -> None:
    settings = Settings()
    filters = RiskFilters(Decimal("0.001"), Decimal("1000"), Decimal("0.001"))
    decision = evaluate_decision(
        symbol="BTCUSDT",
        candles_by_timeframe={"5m": [candle("BTCUSDT", "5m", i, str(100 + i)) for i in range(20)]},
        decision_time_ms=2_000_000,
        settings=settings,
        filters=filters,
    )
    assert decision.status.value == "UNSAFE"
    assert "DATA_UNSAFE" in decision.reason_codes
    assert decision.decision_hash
