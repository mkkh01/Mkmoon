from decimal import Decimal

from app.engine.risk import RiskFilters, calculate_risk_plan, normalize_price


def test_normalize_price_rounds_in_requested_direction() -> None:
    tick = Decimal("0.01")
    assert normalize_price(Decimal("100.001"), tick, "down") == Decimal("100.00")
    assert normalize_price(Decimal("100.001"), tick, "up") == Decimal("100.01")
    assert normalize_price(Decimal("100.000"), tick, "up") == Decimal("100.00")


def test_normalize_price_rejects_unknown_direction() -> None:
    try:
        normalize_price(Decimal("1"), Decimal("0.01"), "sideways")
    except ValueError as exc:
        assert "direction" in str(exc)
    else:
        raise AssertionError("unknown direction must fail closed")


def test_risk_rejects_stop_at_or_above_entry() -> None:
    filters = RiskFilters(Decimal("0.001"), Decimal("1000"), Decimal("0.001"))
    for stop in (Decimal("100"), Decimal("101")):
        plan = calculate_risk_plan(
            equity=Decimal("10000"), risk_pct=Decimal("0.01"),
            remaining_daily_risk=Decimal("100"), remaining_portfolio_risk=Decimal("100"),
            symbol_risk_cap=Decimal("100"), cluster_risk_cap=Decimal("100"),
            entry_price=Decimal("100"), stop_price=stop, target_price=Decimal("102"),
            fee_rate=Decimal("0.001"), slippage_rate=Decimal("0.0005"), filters=filters,
        )
        assert not plan.valid
        assert plan.reason_codes == ["INVALID_PRICE_GEOMETRY"]


def test_risk_accepts_valid_long_geometry() -> None:
    filters = RiskFilters(Decimal("0.001"), Decimal("1000"), Decimal("0.001"))
    plan = calculate_risk_plan(
        equity=Decimal("10000"), risk_pct=Decimal("0.01"),
        remaining_daily_risk=Decimal("100"), remaining_portfolio_risk=Decimal("100"),
        symbol_risk_cap=Decimal("100"), cluster_risk_cap=Decimal("100"),
        entry_price=Decimal("100"), stop_price=Decimal("99"), target_price=Decimal("102"),
        fee_rate=Decimal("0.001"), slippage_rate=Decimal("0.0005"), filters=filters,
    )
    assert plan.valid
    assert plan.quantity > 0
