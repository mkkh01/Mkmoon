from decimal import Decimal
from datetime import datetime, timezone

from app.core.models import Candle, Decision, DecisionStatus, RiskPlan
from app.engine.paper import simulate_long_entry, simulate_long_exit
from app.worker import _cycle_status, _first_exit_fill


def _decision() -> Decision:
    return Decision(
        decision_id="d-paper",
        symbol="BTCUSDT",
        decision_time_ms=1000,
        data_cutoff_ms=900,
        status=DecisionStatus.ENTER,
        entry_price=Decimal("100"),
        stop_price=Decimal("99"),
        target_price=Decimal("102"),
        quality_score=Decimal("80"),
        risk=RiskPlan(
            risk_cash=Decimal("10"), risk_pct=Decimal("0.01"), entry_price=Decimal("100"),
            stop_price=Decimal("99"), target_price=Decimal("102"), unit_risk=Decimal("1"),
            quantity=Decimal("0.1"), effective_risk_cash=Decimal("0.1"),
            expected_cost_cash=Decimal("0.01"), effective_rr=Decimal("2"), valid=True,
        ),
        created_at=datetime.now(timezone.utc),
    )


def test_paper_entry_is_adverse_and_charges_fee() -> None:
    entry = simulate_long_entry(_decision(), fee_rate=Decimal("0.001"), slippage_rate=Decimal("0.01"))
    assert entry is not None
    assert entry.fill_price == Decimal("101.00")
    assert entry.quantity == Decimal("0.1")
    assert entry.fee == Decimal("0.010100")


def test_paper_exit_is_conservative_and_net_of_fee() -> None:
    candle = Candle(
        symbol="BTCUSDT", timeframe="5m", open_time_ms=1, close_time_ms=2,
        open=Decimal("100"), high=Decimal("103"), low=Decimal("98"), close=Decimal("101"),
        volume=Decimal("10"), is_closed=True,
    )
    result = simulate_long_exit(_decision(), candle, fee_rate=Decimal("0.001"))
    assert result.status == "CLOSED"
    assert result.exit_reason == "STOP_AND_TARGET_SAME_CANDLE_CONSERVATIVE_STOP"
    assert result.fill_price == Decimal("99")
    assert result.realized_pnl < Decimal("0")


def test_worker_exit_uses_first_hit_between_cycles() -> None:
    missed_if_latest_only = Candle(
        symbol="BTCUSDT", timeframe="5m", open_time_ms=3, close_time_ms=4,
        open=Decimal("100"), high=Decimal("101"), low=Decimal("99.5"), close=Decimal("100.2"),
        volume=Decimal("10"), is_closed=True,
    )
    earlier_target = Candle(
        symbol="BTCUSDT", timeframe="5m", open_time_ms=1, close_time_ms=2,
        open=Decimal("100"), high=Decimal("102.5"), low=Decimal("99.5"), close=Decimal("101.5"),
        volume=Decimal("10"), is_closed=True,
    )
    exit_time_ms, fill = _first_exit_fill(
        [missed_if_latest_only, earlier_target], entry_price=Decimal("100"),
        stop_price=Decimal("99"), target_price=Decimal("102"), quantity=Decimal("0.1"),
        fee_rate=Decimal("0.001"),
    )
    assert exit_time_ms == 2
    assert fill is not None
    assert fill.exit_reason == "TAKE_PROFIT"


def test_cycle_status_does_not_mark_interrupted_work_as_completed() -> None:
    assert _cycle_status(
        fatal=False, interrupted=True, error_count=0, decisions_count=10,
        audit_write_errors=0, symbols_failed=0, symbols_skipped=0,
    ) == "FAILED"
    assert _cycle_status(
        fatal=False, interrupted=False, error_count=1, decisions_count=10,
        audit_write_errors=0, symbols_failed=0, symbols_skipped=0,
    ) == "PARTIAL"
    assert _cycle_status(
        fatal=False, interrupted=False, error_count=1, decisions_count=10,
        audit_write_errors=0, symbols_failed=1, symbols_skipped=0,
    ) == "PARTIAL"
    assert _cycle_status(
        fatal=False, interrupted=False, error_count=0, decisions_count=25,
        audit_write_errors=0, symbols_failed=0, symbols_skipped=0,
    ) == "COMPLETED"
