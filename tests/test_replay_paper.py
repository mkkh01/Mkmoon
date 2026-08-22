from decimal import Decimal

from app.core.models import Candle, DecisionStatus
from app.core.settings import Settings
from app.engine.paper import simulate_long_exit
from app.engine.replay import replay_decisions
from app.engine.risk import RiskFilters


def make_candle(index: int, close: str, timeframe: str = "5m") -> dict:
    price = Decimal(close)
    return {
        "symbol": "BTCUSDT", "timeframe": timeframe, "open_time_ms": index * 60_000,
        "close_time_ms": index * 60_000 + 59_000, "open": price - Decimal("0.1"),
        "high": price + Decimal("0.2"), "low": price - Decimal("0.2"), "close": price,
        "volume": Decimal("100"), "is_closed": True, "source_id": "fixture",
    }


def test_replay_same_snapshot_has_same_hash() -> None:
    candles = {"5m": [make_candle(i, str(100 + i)) for i in range(20)]}
    snapshot = {"symbol": "BTCUSDT", "decision_time_ms": 2_000_000, "candles_by_timeframe": candles}
    settings = Settings()
    filters = {"BTCUSDT": RiskFilters(Decimal("0.001"), Decimal("1000"), Decimal("0.001"))}
    first = replay_decisions([snapshot], settings, filters)[0]
    second = replay_decisions([snapshot], settings, filters)[0]
    assert first.decision_hash == second.decision_hash
    assert first.created_at == second.created_at


def test_paper_uses_conservative_stop_on_ambiguous_candle() -> None:
    from app.core.models import RiskPlan, Decision
    from datetime import datetime, timezone

    decision = Decision(
        decision_id="d1", symbol="BTCUSDT", decision_time_ms=1, data_cutoff_ms=1,
        status=DecisionStatus.ENTER, entry_price=Decimal("100"), stop_price=Decimal("99"),
        target_price=Decimal("102"), quality_score=Decimal("80"), component_scores={},
        risk=RiskPlan(risk_cash=Decimal("10"), risk_pct=Decimal("0.01"), entry_price=Decimal("100"),
                      stop_price=Decimal("99"), target_price=Decimal("102"), unit_risk=Decimal("1"),
                      quantity=Decimal("10"), effective_risk_cash=Decimal("10"), expected_cost_cash=Decimal("1"),
                      effective_rr=Decimal("2"), valid=True), created_at=datetime.now(timezone.utc),
    )
    candle = Candle(symbol="BTCUSDT", timeframe="5m", open_time_ms=1, close_time_ms=2,
                    open=Decimal("100"), high=Decimal("103"), low=Decimal("98"), close=Decimal("101"),
                    volume=Decimal("10"), is_closed=True)
    result = simulate_long_exit(decision, candle)
    assert result.status == "CLOSED"
    assert result.exit_reason.startswith("STOP_AND_TARGET")
    assert result.fill_price == Decimal("99")
